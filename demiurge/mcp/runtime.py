from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import re
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol

from demiurge.core import LoadedCore, McpServerDefinition
from demiurge.mcp.security import (
    mcp_server_fingerprint,
    mcp_server_identity_payload,
)
from demiurge.providers import ToolDefinition
from demiurge.sdk import ToolResult, TurnContext
from demiurge.security.subprocess_env import (
    build_sanitized_subprocess_env,
    ensure_subprocess_home,
)
from demiurge.security.url_policy import (
    UrlDecision,
    UrlPolicy,
    UrlPolicyAsyncTransport,
)
from demiurge.storage import EventLog


TOOL_NAME_SEPARATOR = "__"
TOOL_NAME_MAX_PREFIX = 30
TOOL_NAME_MAX_TOTAL = 64
TOOL_NAME_SAFE_RE = re.compile(r"[^A-Za-z0-9_-]")
ENV_REF_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
MCP_DISCOVERY_CONCURRENCY = 4


@dataclass(frozen=True, slots=True)
class McpCatalogScopeKey:
    session_id: str
    core_root: str
    workspace: str


@dataclass(frozen=True, slots=True)
class McpCatalogKey:
    session_id: str
    authority_key: str
    core_root: str
    core_revision: str
    workspace: str
    fingerprint: str

    @property
    def scope(self) -> McpCatalogScopeKey:
        return McpCatalogScopeKey(
            session_id=self.session_id,
            core_root=self.core_root,
            workspace=self.workspace,
        )


@dataclass(frozen=True, slots=True)
class McpConnectionKey:
    session_id: str
    authority_key: str
    core_root: str
    core_revision: str
    workspace: str
    server_id: str
    server_fingerprint: str

    @property
    def scope(self) -> McpCatalogScopeKey:
        return McpCatalogScopeKey(
            session_id=self.session_id,
            core_root=self.core_root,
            workspace=self.workspace,
        )

    def adapter_identity(self) -> str:
        return ":".join(
            (
                self.session_id,
                self.authority_key,
                self.core_root,
                self.core_revision,
                self.workspace,
                self.server_id,
                self.server_fingerprint,
            )
        )


class McpRuntimeError(RuntimeError):
    pass


@dataclass(slots=True)
class McpCatalogDiagnostic:
    server_id: str
    relative_path: str
    message: str


@dataclass(slots=True)
class McpToolInfo:
    name: str
    server_id: str
    server_tool_name: str
    description: str
    input_schema: dict[str, Any]
    relative_path: str
    risk: str
    approval_policy: str
    capability: str
    timeout_seconds: float
    connection_key: McpConnectionKey

    def to_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name,
            description=self.description,
            input_schema=self.input_schema or {"type": "object", "properties": {}},
        )


@dataclass(slots=True)
class McpCatalog:
    fingerprint: str
    tools: list[McpToolInfo] = field(default_factory=list)
    diagnostics: list[McpCatalogDiagnostic] = field(default_factory=list)
    server_states: dict[str, "McpServerCatalogState"] = field(
        default_factory=dict
    )


@dataclass(frozen=True, slots=True)
class McpServerCatalogState:
    status: str
    server_fingerprint: str
    retry_after: float | None = None
    diagnostic: McpCatalogDiagnostic | None = None


class McpClientConnection(Protocol):
    async def list_tools(self) -> list[Any]:
        ...

    async def call_tool(self, name: str, arguments: dict[str, Any], *, timeout_seconds: float) -> Any:
        ...

    async def close(self) -> None:
        ...


@dataclass(slots=True)
class _McpDiscovery:
    server: McpServerDefinition
    connection: McpClientConnection | None = None
    tools: list[Any] = field(default_factory=list)
    error: str | None = None


McpClientFactory = Callable[[McpServerDefinition, dict[str, str], dict[str, str], Path, Path], McpClientConnection]
McpConnectAuthorizer = Callable[[McpServerDefinition], Awaitable[bool]]
EventEmitter = Callable[..., dict[str, Any]]


def _set_url_decision_sink(
    connection: McpClientConnection,
    sink: Callable[[UrlDecision], None],
) -> None:
    setter = getattr(connection, "set_url_decision_sink", None)
    if callable(setter):
        setter(sink)


class McpRuntime:
    def __init__(
        self,
        *,
        home: Path,
        workspace: Path,
        client_factory: McpClientFactory | None = None,
        url_policy: UrlPolicy | None = None,
        failure_cache_ttl_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if failure_cache_ttl_seconds <= 0:
            raise ValueError("MCP failure cache TTL must be positive")
        self.home = home
        self.workspace = workspace
        self.url_policy = url_policy or UrlPolicy()
        self.client_factory = client_factory or (
            lambda server, env, headers, workspace, stderr_log_path: (
                DefaultMcpClientConnection(
                    server,
                    env,
                    headers,
                    workspace,
                    stderr_log_path,
                    url_policy=self.url_policy,
                )
            )
        )
        self.failure_cache_ttl_seconds = float(failure_cache_ttl_seconds)
        self._clock = clock
        self._catalogs: dict[McpCatalogKey, McpCatalog] = {}
        self._connections: dict[McpConnectionKey, McpClientConnection] = {}
        self._catalog_locks: dict[McpCatalogScopeKey, asyncio.Lock] = {}
        self._discovery_semaphore = asyncio.Semaphore(
            MCP_DISCOVERY_CONCURRENCY
        )
        self._build_tasks: dict[
            asyncio.Task[
                tuple[
                    McpCatalog,
                    dict[McpConnectionKey, McpClientConnection],
                ]
            ],
            McpCatalogKey,
        ] = {}
        self._session_generations: dict[str, int] = {}
        self._lock = asyncio.Lock()
        self._closed = False

    async def prepare_for_turn(
        self,
        core: LoadedCore,
        turn: TurnContext,
        *,
        authority_key: str = "unbound",
        authorize_server: McpConnectAuthorizer | None = None,
        emit_event: EventEmitter | None = None,
    ) -> McpCatalog:
        if (
            authorize_server is None
            or not authority_key.strip()
            or authority_key == "unbound"
        ):
            raise McpRuntimeError(
                "MCP prepare requires bound connect authority"
            )
        session_generation = self._session_generations.get(
            turn.session_id,
            0,
        )
        fingerprint = self._fingerprint(core)
        catalog_key = McpCatalogKey(
            session_id=turn.session_id,
            authority_key=authority_key,
            core_root=str(core.root),
            core_revision=turn.core_revision,
            workspace=str(self.workspace),
            fingerprint=fingerprint,
        )
        scope_key = catalog_key.scope
        async with self._lock:
            if self._closed:
                raise McpRuntimeError("MCP runtime is closed")
            if (
                self._session_generations.get(turn.session_id, 0)
                != session_generation
            ):
                raise McpRuntimeError(
                    "MCP session was evicted before discovery"
                )
            catalog_lock = self._catalog_locks.setdefault(
                scope_key,
                asyncio.Lock(),
            )
        async with catalog_lock:
            connections_to_close: list[McpClientConnection] = []
            cached_catalog: McpCatalog | None = None
            refresh_catalog: McpCatalog | None = None
            async with self._lock:
                if self._closed:
                    raise McpRuntimeError("MCP runtime is closed")
                stale_keys = [
                    key
                    for key in self._catalogs
                    if key.scope == scope_key and key != catalog_key
                ]
                for stale_key in stale_keys:
                    if (
                        stale_key.authority_key == catalog_key.authority_key
                        and refresh_catalog is None
                    ):
                        refresh_catalog = self._catalogs[stale_key]
                    else:
                        connections_to_close.extend(
                            self._pop_catalog_locked(stale_key)
                        )
                cached = self._catalogs.get(catalog_key)
                if cached is not None:
                    if not self._catalog_needs_refresh(cached, core):
                        cached_catalog = cached
                    else:
                        refresh_catalog = cached
            await self._close_connections(connections_to_close)
            if cached_catalog is not None:
                return cached_catalog

            async with self._lock:
                if self._closed:
                    raise McpRuntimeError(
                        "MCP runtime closed before discovery"
                    )
                if (
                    self._session_generations.get(turn.session_id, 0)
                    != session_generation
                ):
                    raise McpRuntimeError(
                        "MCP session was evicted before discovery"
                    )
                build_task = asyncio.create_task(
                    self._build_catalog(
                        core,
                        catalog_key=catalog_key,
                        fingerprint=fingerprint,
                        authorize_server=authorize_server,
                        emit_event=emit_event,
                        cached=refresh_catalog,
                    )
                )
                self._build_tasks[build_task] = catalog_key
            try:
                catalog, discovered_connections = await build_task
            finally:
                async with self._lock:
                    self._build_tasks.pop(build_task, None)
            orphan_connections: list[McpClientConnection] = []
            async with self._lock:
                session_evicted = (
                    self._session_generations.get(turn.session_id, 0)
                    != session_generation
                )
                if self._closed or session_evicted:
                    publish = False
                else:
                    self._connections.update(discovered_connections)
                    self._catalogs[catalog_key] = catalog
                    for obsolete_key in [
                        key
                        for key in self._catalogs
                        if key.scope == scope_key and key != catalog_key
                    ]:
                        self._catalogs.pop(obsolete_key, None)
                    referenced_keys = {
                        tool.connection_key
                        for current_catalog in self._catalogs.values()
                        for tool in current_catalog.tools
                    }
                    for connection_key in [
                        key
                        for key in self._connections
                        if key.scope == scope_key
                        and key not in referenced_keys
                    ]:
                        orphan_connections.append(
                            self._connections.pop(connection_key)
                        )
                    publish = True
            if not publish:
                await self._close_connections(
                    list(discovered_connections.values())
                )
                if session_evicted:
                    raise McpRuntimeError(
                        "MCP session was evicted during discovery"
                    )
                raise McpRuntimeError("MCP runtime closed during discovery")
            await self._close_connections(orphan_connections)
            return catalog

    def entries_for(
        self,
        core: LoadedCore,
        *,
        turn: TurnContext | None = None,
    ) -> list[McpToolInfo]:
        fingerprint = self._fingerprint(core)
        tools: list[McpToolInfo] = []
        seen: set[str] = set()
        for key, catalog in self._catalogs.items():
            if (
                key.core_root != str(core.root)
                or key.workspace != str(self.workspace)
                or key.fingerprint != fingerprint
            ):
                continue
            if turn is not None:
                if key.session_id != turn.session_id:
                    continue
                if key.core_revision != turn.core_revision:
                    continue
            for tool in catalog.tools:
                if tool.name in seen:
                    continue
                seen.add(tool.name)
                tools.append(tool)
        return sorted(tools, key=lambda tool: tool.name)

    async def call_tool(
        self,
        tool: McpToolInfo,
        arguments: dict[str, Any],
    ) -> ToolResult:
        connection = self._connection_for_tool(tool)
        if connection is None:
            return ToolResult(
                content=f"MCP server is not connected for tool: {tool.name}",
                is_error=True,
                data={"executionStarted": False, "mcpServer": tool.server_id, "mcpTool": tool.server_tool_name},
            )
        try:
            result = await connection.call_tool(
                tool.server_tool_name,
                arguments if isinstance(arguments, dict) else {},
                timeout_seconds=tool.timeout_seconds,
            )
        except Exception as exc:
            return ToolResult(
                content=f"MCP tool failed: {exc}",
                is_error=True,
                data={
                    "executionStarted": True,
                    "mcpServer": tool.server_id,
                    "mcpTool": tool.server_tool_name,
                },
            )
        return mcp_result_to_tool_result(tool, result)

    async def evict_session(self, session_id: str) -> None:
        async with self._lock:
            self._session_generations[session_id] = (
                self._session_generations.get(session_id, 0) + 1
            )
            build_tasks = [
                task
                for task, key in self._build_tasks.items()
                if key.session_id == session_id
            ]
            for task in build_tasks:
                task.cancel()
            session_locks = [
                lock
                for scope_key, lock in self._catalog_locks.items()
                if scope_key.session_id == session_id
            ]
        await asyncio.gather(*build_tasks, return_exceptions=True)
        acquired: list[asyncio.Lock] = []
        connections: list[McpClientConnection] = []
        try:
            for lock in session_locks:
                await lock.acquire()
                acquired.append(lock)
            async with self._lock:
                catalog_keys = [
                    key
                    for key in self._catalogs
                    if key.session_id == session_id
                ]
                for catalog_key in catalog_keys:
                    connections.extend(
                        self._pop_catalog_locked(catalog_key)
                    )
                stale_connection_keys = [
                    key
                    for key in self._connections
                    if key.session_id == session_id
                ]
                connections.extend(
                    self._connections.pop(key)
                    for key in stale_connection_keys
                )
                for scope_key in [
                    key
                    for key in self._catalog_locks
                    if key.session_id == session_id
                ]:
                    self._catalog_locks.pop(scope_key, None)
        finally:
            for lock in reversed(acquired):
                lock.release()
        await self._close_connections(connections)

    async def close(self) -> None:
        async with self._lock:
            self._closed = True
            build_tasks = list(self._build_tasks)
            self._build_tasks.clear()
            connections = list(self._connections.values())
            self._connections.clear()
            self._catalogs.clear()
            self._catalog_locks.clear()
            self._session_generations.clear()
        for task in build_tasks:
            task.cancel()
        await asyncio.gather(*build_tasks, return_exceptions=True)
        await self._close_connections(connections)

    async def _build_catalog(
        self,
        core: LoadedCore,
        *,
        catalog_key: McpCatalogKey,
        fingerprint: str,
        authorize_server: McpConnectAuthorizer | None,
        emit_event: EventEmitter | None,
        cached: McpCatalog | None,
    ) -> tuple[McpCatalog, dict[McpConnectionKey, McpClientConnection]]:
        catalog = McpCatalog(fingerprint=fingerprint)
        connections: dict[McpConnectionKey, McpClientConnection] = {}
        reserved_names: set[str] = set()
        used_server_names: set[str] = set()
        enabled_servers = [
            server for server in core.mcp_servers if server.enabled
        ]
        safe_server_names = {
            server.server_id: sanitize_server_name(
                server.server_id,
                used_server_names,
            )
            for server in enabled_servers
        }
        server_fingerprints = {
            server.server_id: self._server_fingerprint(server)
            for server in enabled_servers
        }
        authorized_servers: list[McpServerDefinition] = []
        for server in enabled_servers:
            server_fingerprint = server_fingerprints[server.server_id]
            cached_state = (
                cached.server_states.get(server.server_id)
                if cached is not None
                else None
            )
            if (
                cached_state is not None
                and cached_state.status == "connected"
                and cached_state.server_fingerprint == server_fingerprint
            ):
                catalog.server_states[server.server_id] = cached_state
                cached_tools = [
                    tool
                    for tool in cached.tools
                    if tool.server_id == server.server_id
                ]
                if all(
                    self._connection_for_tool(tool) is not None
                    for tool in cached_tools
                ):
                    catalog.tools.extend(cached_tools)
                    reserved_names.update(
                        _normalize_tool_name(tool.name)
                        for tool in cached_tools
                    )
                    continue
            if (
                cached_state is not None
                and cached_state.status == "failed"
                and cached_state.server_fingerprint == server_fingerprint
                and cached_state.retry_after is not None
                and self._clock() < cached_state.retry_after
            ):
                catalog.server_states[server.server_id] = cached_state
                if cached_state.diagnostic is not None:
                    catalog.diagnostics.append(cached_state.diagnostic)
                continue
            if authorize_server is not None and not await authorize_server(server):
                catalog.server_states[server.server_id] = (
                    McpServerCatalogState(
                        status="denied",
                        server_fingerprint=server_fingerprint,
                        retry_after=self._clock(),
                    )
                )
                continue
            authorized_servers.append(server)

        discovery_tasks = [
            asyncio.create_task(
                self._discover_server(
                    server,
                    semaphore=self._discovery_semaphore,
                    session_id=catalog_key.session_id,
                )
            )
            for server in authorized_servers
        ]
        try:
            discoveries = await asyncio.gather(*discovery_tasks)
            for discovery in discoveries:
                server = discovery.server
                server_fingerprint = server_fingerprints[server.server_id]
                if discovery.error is not None:
                    diagnostic = self._diagnose(
                        catalog,
                        server,
                        discovery.error,
                        emit_event=emit_event,
                    )
                    catalog.server_states[server.server_id] = (
                        McpServerCatalogState(
                            status="failed",
                            server_fingerprint=server_fingerprint,
                            retry_after=(
                                self._clock()
                                + self.failure_cache_ttl_seconds
                            ),
                            diagnostic=diagnostic,
                        )
                    )
                    continue
                connection = discovery.connection
                if connection is None:
                    diagnostic = self._diagnose(
                        catalog,
                        server,
                        "MCP discovery did not return a connection",
                        emit_event=emit_event,
                    )
                    catalog.server_states[server.server_id] = (
                        McpServerCatalogState(
                            status="failed",
                            server_fingerprint=server_fingerprint,
                            retry_after=(
                                self._clock()
                                + self.failure_cache_ttl_seconds
                            ),
                            diagnostic=diagnostic,
                        )
                    )
                    continue
                safe_server_name = safe_server_names[server.server_id]
                connection_key = McpConnectionKey(
                    session_id=catalog_key.session_id,
                    authority_key=catalog_key.authority_key,
                    core_root=catalog_key.core_root,
                    core_revision=catalog_key.core_revision,
                    workspace=catalog_key.workspace,
                    server_id=server.server_id,
                    server_fingerprint=server_fingerprint,
                )
                connections[connection_key] = connection
                catalog.server_states[server.server_id] = (
                    McpServerCatalogState(
                        status="connected",
                        server_fingerprint=server_fingerprint,
                    )
                )
                for listed_tool in discovery.tools:
                    tool_name = str(
                        getattr(listed_tool, "name", "") or ""
                    ).strip()
                    if not tool_name or not self._tool_selected(
                        server,
                        tool_name,
                    ):
                        continue
                    safe_tool_name = build_safe_tool_name(
                        server_name=safe_server_name,
                        tool_name=tool_name,
                        reserved_names=reserved_names,
                    )
                    reserved_names.add(_normalize_tool_name(safe_tool_name))
                    catalog.tools.append(
                        McpToolInfo(
                            name=safe_tool_name,
                            server_id=server.server_id,
                            server_tool_name=tool_name,
                            description=self._tool_description(
                                server,
                                listed_tool,
                            ),
                            input_schema=self._tool_schema(listed_tool),
                            relative_path=server.relative_path,
                            risk=server.manifest.risk,
                            approval_policy=server.manifest.approval_policy,
                            capability=server.capability,
                            timeout_seconds=server.manifest.timeout_seconds,
                            connection_key=connection_key,
                        )
                    )
        except BaseException:
            results = await asyncio.gather(
                *discovery_tasks,
                return_exceptions=True,
            )
            pending_connections = [
                result.connection
                for result in results
                if isinstance(result, _McpDiscovery)
                and result.connection is not None
            ]
            await self._close_connections(
                [*connections.values(), *pending_connections]
            )
            raise
        catalog.tools.sort(key=lambda tool: tool.name)
        catalog.diagnostics.sort(key=lambda item: item.server_id)
        return catalog, connections

    def _pop_catalog_locked(
        self,
        catalog_key: McpCatalogKey,
    ) -> list[McpClientConnection]:
        catalog = self._catalogs.pop(catalog_key, None)
        if catalog is None:
            return []
        candidate_keys = {tool.connection_key for tool in catalog.tools}
        referenced_keys = {
            tool.connection_key
            for current_catalog in self._catalogs.values()
            for tool in current_catalog.tools
        }
        stale_keys = {
            key
            for key in candidate_keys - referenced_keys
            if key in self._connections
        }
        return [self._connections.pop(key) for key in stale_keys]

    async def _close_connections(
        self,
        connections: list[McpClientConnection],
    ) -> None:
        unique = list({id(connection): connection for connection in connections}.values())
        await asyncio.gather(
            *(connection.close() for connection in unique),
            return_exceptions=True,
        )

    async def _discover_server(
        self,
        server: McpServerDefinition,
        *,
        semaphore: asyncio.Semaphore,
        session_id: str,
    ) -> _McpDiscovery:
        async with semaphore:
            try:
                env = interpolate_env_map(server.manifest.env)
                headers = interpolate_env_map(server.manifest.headers)
            except KeyError as exc:
                return _McpDiscovery(
                    server=server,
                    error=f"missing environment variable: {exc.args[0]}",
                )
            connection: McpClientConnection | None = None
            try:
                connection = self.client_factory(
                    server,
                    env,
                    headers,
                    self.workspace,
                    self._stderr_log_path(),
                )
                event_log = EventLog(self.home, session_id)
                _set_url_decision_sink(
                    connection,
                    lambda decision: event_log.emit(
                        "mcp.url_decision",
                        server_id=server.server_id,
                        phase="request",
                        url_policy=decision.audit_view(),
                    ),
                )
                try:
                    tools = await asyncio.wait_for(
                        connection.list_tools(),
                        timeout=server.manifest.connect_timeout_seconds,
                    )
                except TimeoutError as exc:
                    raise McpRuntimeError(
                        "MCP discovery timed out after "
                        f"{server.manifest.connect_timeout_seconds:g}s"
                    ) from exc
            except asyncio.CancelledError:
                if connection is not None:
                    with contextlib.suppress(Exception):
                        await connection.close()
                raise
            except Exception as exc:
                if connection is not None:
                    with contextlib.suppress(Exception):
                        await connection.close()
                return _McpDiscovery(server=server, error=str(exc))
            return _McpDiscovery(
                server=server,
                connection=connection,
                tools=list(tools),
            )

    def _connection_for_tool(self, tool: McpToolInfo) -> McpClientConnection | None:
        return self._connections.get(tool.connection_key)

    def _catalog_needs_refresh(
        self,
        catalog: McpCatalog,
        core: LoadedCore,
    ) -> bool:
        for server in core.mcp_servers:
            if not server.enabled:
                continue
            state = catalog.server_states.get(server.server_id)
            if (
                state is None
                or state.server_fingerprint
                != self._server_fingerprint(server)
                or state.status == "denied"
            ):
                return True
            if (
                state.status == "failed"
                and state.retry_after is not None
                and self._clock() >= state.retry_after
            ):
                return True
        return False

    def _diagnose(
        self,
        catalog: McpCatalog,
        server: McpServerDefinition,
        message: str,
        *,
        emit_event: EventEmitter | None,
    ) -> McpCatalogDiagnostic:
        diagnostic = McpCatalogDiagnostic(
            server_id=server.server_id,
            relative_path=server.relative_path,
            message=message,
        )
        catalog.diagnostics.append(diagnostic)
        if emit_event is not None:
            emit_event(
                "mcp.server_failed",
                server_id=server.server_id,
                path=server.relative_path,
                message=message,
            )
        return diagnostic

    def _tool_selected(self, server: McpServerDefinition, tool_name: str) -> bool:
        include = server.manifest.tools.include
        exclude = server.manifest.tools.exclude
        if include and not any(glob_matches(pattern, tool_name) for pattern in include):
            return False
        return not any(glob_matches(pattern, tool_name) for pattern in exclude)

    def _tool_description(self, server: McpServerDefinition, listed_tool: Any) -> str:
        description = str(getattr(listed_tool, "description", "") or "").strip()
        if description:
            return description
        return f"Tool `{getattr(listed_tool, 'name', 'unknown')}` from MCP server `{server.server_id}`."

    def _tool_schema(self, listed_tool: Any) -> dict[str, Any]:
        schema = getattr(listed_tool, "inputSchema", None)
        if schema is None:
            schema = getattr(listed_tool, "input_schema", None)
        if not isinstance(schema, dict):
            return {"type": "object", "properties": {}}
        if not schema:
            return {"type": "object", "properties": {}}
        return dict(schema)

    def _fingerprint(self, core: LoadedCore) -> str:
        payload = [
            mcp_server_identity_payload(server)
            for server in core.mcp_servers
        ]
        text = json.dumps(payload, ensure_ascii=True, sort_keys=True, default=str)
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _server_fingerprint(self, server: McpServerDefinition) -> str:
        return mcp_server_fingerprint(server)

    def _stderr_log_path(self) -> Path:
        log_dir = self.home / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        return log_dir / "mcp-stderr.log"


class DefaultMcpClientConnection:
    def __init__(
        self,
        server: McpServerDefinition,
        env: dict[str, str],
        headers: dict[str, str],
        workspace: Path,
        stderr_log_path: Path,
        *,
        url_policy: UrlPolicy | None = None,
        httpx_transport_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.server = server
        self.env = env
        self.headers = headers
        self.workspace = workspace
        self.stderr_log_path = stderr_log_path
        self.url_policy = url_policy or UrlPolicy()
        self._httpx_transport_factory = httpx_transport_factory
        self._url_decision_sink: Callable[[UrlDecision], None] | None = None
        self._stack: AsyncExitStack | None = None
        self._session: Any | None = None
        self._stderr_fh: Any | None = None

    async def list_tools(self) -> list[Any]:
        session = await self._ensure_session()
        result = await session.list_tools()
        return list(getattr(result, "tools", []) or [])

    async def call_tool(self, name: str, arguments: dict[str, Any], *, timeout_seconds: float) -> Any:
        session = await self._ensure_session()
        return await asyncio.wait_for(session.call_tool(name, arguments), timeout=timeout_seconds)

    async def close(self) -> None:
        stack = self._stack
        self._stack = None
        self._session = None
        if stack is not None:
            with contextlib.suppress(Exception):
                await stack.aclose()
        if self._stderr_fh is not None:
            with contextlib.suppress(Exception):
                self._stderr_fh.close()
            self._stderr_fh = None

    async def _ensure_session(self) -> Any:
        if self._session is not None:
            return self._session
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
            from mcp.client.streamable_http import streamable_http_client
        except ImportError as exc:
            raise McpRuntimeError("mcp package is not installed") from exc

        stack = AsyncExitStack()
        self._stack = stack
        try:
            if self.server.manifest.transport == "stdio":
                params = StdioServerParameters(
                    command=str(self.server.manifest.command),
                    args=list(self.server.manifest.args),
                    env=self._stdio_env(),
                    cwd=self._cwd(),
                )
                self._stderr_fh = self.stderr_log_path.open("a", encoding="utf-8", errors="replace", buffering=1)
                self._stderr_fh.write(f"\n===== starting MCP server '{self.server.server_id}' =====\n")
                read_stream, write_stream = await stack.enter_async_context(stdio_client(params, errlog=self._stderr_fh))
            else:
                import httpx

                http_client = self._httpx_client_factory(
                    headers=self.headers or None,
                    timeout=httpx.Timeout(
                        self.server.manifest.connect_timeout_seconds,
                        read=max(
                            self.server.manifest.timeout_seconds,
                            60,
                        ),
                    ),
                    auth=None,
                )
                await stack.enter_async_context(http_client)
                read_stream, write_stream, _get_session_id = await stack.enter_async_context(
                    streamable_http_client(
                        str(self.server.manifest.url),
                        http_client=http_client,
                    )
                )
            session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
            await asyncio.wait_for(session.initialize(), timeout=self.server.manifest.connect_timeout_seconds)
            self._session = session
            return session
        except Exception:
            await self.close()
            raise

    def set_url_decision_sink(
        self,
        sink: Callable[[UrlDecision], None],
    ) -> None:
        self._url_decision_sink = sink

    def _httpx_client_factory(
        self,
        headers: dict[str, str] | None = None,
        timeout: Any | None = None,
        auth: Any | None = None,
    ) -> Any:
        import httpx

        kwargs: dict[str, Any] = {
            "follow_redirects": True,
            "transport": UrlPolicyAsyncTransport(
                self.url_policy,
                transport_factory=self._httpx_transport_factory,
                decision_sink=self._record_url_decision,
            ),
        }
        if headers is not None:
            kwargs["headers"] = headers
        if timeout is not None:
            kwargs["timeout"] = timeout
        if auth is not None:
            kwargs["auth"] = auth
        return httpx.AsyncClient(**kwargs)

    def _record_url_decision(self, decision: UrlDecision) -> None:
        sink = self._url_decision_sink
        if sink is not None:
            sink(decision)

    def _cwd(self) -> Path:
        cwd = self.server.manifest.cwd
        if not cwd:
            return self.workspace.resolve()
        path = Path(cwd).expanduser()
        if not path.is_absolute():
            path = self.workspace / path
        return path.resolve()

    def _stdio_env(self) -> dict[str, str]:
        home = ensure_subprocess_home(
            self.stderr_log_path.parent.parent / "mcp-home"
        )
        return build_sanitized_subprocess_env(
            os.environ,
            self.env,
            home=home,
        )


def mcp_result_to_tool_result(tool: McpToolInfo, result: Any) -> ToolResult:
    structured = getattr(result, "structuredContent", None)
    if structured is None:
        structured = getattr(result, "structured_content", None)
    is_error = bool(getattr(result, "isError", False) or getattr(result, "is_error", False))
    if structured is not None:
        content = json.dumps(structured, ensure_ascii=False, indent=2)
        return ToolResult(
            content=content,
            data=_result_data(tool, result, structured=structured),
            is_error=is_error,
            model_output=content,
        )
    content_blocks = getattr(result, "content", None)
    parts = [_content_block_text(block) for block in content_blocks or []]
    content = "\n".join(part for part in parts if part).strip()
    if not content:
        content = json.dumps({"status": "error" if is_error else "ok", "mcpServer": tool.server_id, "mcpTool": tool.server_tool_name})
    return ToolResult(
        content=content,
        data=_result_data(tool, result),
        is_error=is_error,
        model_output=content,
    )


def _result_data(tool: McpToolInfo, result: Any, *, structured: Any | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {
        "executionStarted": True,
        "mcpServer": tool.server_id,
        "mcpTool": tool.server_tool_name,
    }
    if structured is not None:
        data["structuredContent"] = structured
    if bool(getattr(result, "isError", False) or getattr(result, "is_error", False)):
        data["status"] = "error"
    return data


def _content_block_text(block: Any) -> str:
    block_type = getattr(block, "type", None)
    if block_type == "text":
        return str(getattr(block, "text", "") or "")
    if block_type == "image":
        mime_type = getattr(block, "mimeType", None) or getattr(block, "mime_type", None) or "image"
        return f"[image {mime_type}]"
    if block_type == "audio":
        mime_type = getattr(block, "mimeType", None) or getattr(block, "mime_type", None) or "audio"
        return f"[audio {mime_type}]"
    if block_type == "resource_link":
        uri = getattr(block, "uri", "")
        title = getattr(block, "title", None) or getattr(block, "name", None)
        return f"[{title}] {uri}" if title else str(uri)
    if block_type == "resource":
        resource = getattr(block, "resource", None)
        if resource is not None:
            return str(getattr(resource, "text", None) or getattr(resource, "uri", "") or "")
    if isinstance(block, dict):
        if block.get("type") == "text":
            return str(block.get("text") or "")
        return json.dumps(block, ensure_ascii=False)
    return str(block)


def interpolate_env_map(values: dict[str, str]) -> dict[str, str]:
    resolved: dict[str, str] = {}
    for key, value in values.items():
        resolved[key] = interpolate_env_string(str(value))
    return resolved


def interpolate_env_string(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in os.environ:
            raise KeyError(name)
        return os.environ[name]

    return ENV_REF_RE.sub(replace, value)


def sanitize_server_name(raw: str, used_names: set[str]) -> str:
    base = _sanitize_fragment(raw, "mcp", TOOL_NAME_MAX_PREFIX)
    candidate = base
    index = 2
    while _normalize_tool_name(candidate) in used_names:
        suffix = f"-{index}"
        candidate = f"{base[:max(1, TOOL_NAME_MAX_PREFIX - len(suffix))]}{suffix}"
        index += 1
    used_names.add(_normalize_tool_name(candidate))
    return candidate


def build_safe_tool_name(*, server_name: str, tool_name: str, reserved_names: set[str]) -> str:
    cleaned_tool_name = _sanitize_fragment(tool_name, "tool")
    max_tool_chars = max(1, TOOL_NAME_MAX_TOTAL - len(server_name) - len(TOOL_NAME_SEPARATOR))
    truncated = cleaned_tool_name[:max_tool_chars] or "tool"
    candidate_tool = truncated
    candidate = f"{server_name}{TOOL_NAME_SEPARATOR}{candidate_tool}"
    index = 2
    while _normalize_tool_name(candidate) in reserved_names:
        suffix = f"-{index}"
        candidate_tool = f"{truncated[:max(1, max_tool_chars - len(suffix))]}{suffix}"
        candidate = f"{server_name}{TOOL_NAME_SEPARATOR}{candidate_tool}"
        index += 1
    return candidate


def _sanitize_fragment(raw: str, fallback: str, max_chars: int | None = None) -> str:
    cleaned = TOOL_NAME_SAFE_RE.sub("-", raw.strip())
    normalized = cleaned or fallback
    provider_safe = normalized if normalized[0].isalpha() else f"{fallback}-{normalized}"
    if max_chars is not None:
        return provider_safe[:max_chars]
    return provider_safe


def _normalize_tool_name(value: str) -> str:
    return value.strip().lower()


def glob_matches(pattern: str, value: str) -> bool:
    trimmed = pattern.strip()
    if not trimmed:
        return False
    if "*" not in trimmed:
        return trimmed == value
    regex = "^" + ".*".join(re.escape(part) for part in trimmed.split("*")) + "$"
    return re.match(regex, value) is not None
