from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import re
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from demiurge.core import LoadedCore, McpServerDefinition
from demiurge.providers import ToolDefinition
from demiurge.sdk import ToolResult, TurnContext


TOOL_NAME_SEPARATOR = "__"
TOOL_NAME_MAX_PREFIX = 30
TOOL_NAME_MAX_TOTAL = 64
TOOL_NAME_SAFE_RE = re.compile(r"[^A-Za-z0-9_-]")
ENV_REF_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


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
    connection_key: tuple[str, str, str, str, str]

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


class McpClientConnection(Protocol):
    async def list_tools(self) -> list[Any]:
        ...

    async def call_tool(self, name: str, arguments: dict[str, Any], *, timeout_seconds: float) -> Any:
        ...

    async def close(self) -> None:
        ...


McpClientFactory = Callable[[McpServerDefinition, dict[str, str], dict[str, str], Path, Path], McpClientConnection]
EventEmitter = Callable[..., dict[str, Any]]


class McpRuntime:
    def __init__(
        self,
        *,
        home: Path,
        workspace: Path,
        client_factory: McpClientFactory | None = None,
    ) -> None:
        self.home = home
        self.workspace = workspace
        self.client_factory = client_factory or DefaultMcpClientConnection
        self._catalogs: dict[tuple[str, str, str, str], McpCatalog] = {}
        self._connections: dict[tuple[str, str, str, str, str], McpClientConnection] = {}
        self._tool_index: dict[str, McpToolInfo] = {}
        self._lock = asyncio.Lock()

    async def prepare_for_turn(
        self,
        core: LoadedCore,
        turn: TurnContext,
        *,
        emit_event: EventEmitter | None = None,
    ) -> McpCatalog:
        fingerprint = self._fingerprint(core)
        catalog_key = (turn.session_id, str(core.root), str(self.workspace), fingerprint)
        async with self._lock:
            cached = self._catalogs.get(catalog_key)
            if cached is not None:
                return cached
            catalog = await self._build_catalog(core, catalog_key=catalog_key, fingerprint=fingerprint, emit_event=emit_event)
            self._catalogs[catalog_key] = catalog
            for tool in catalog.tools:
                self._tool_index[tool.name] = tool
            return catalog

    def entries_for(self, core: LoadedCore) -> list[McpToolInfo]:
        fingerprint = self._fingerprint(core)
        tools: list[McpToolInfo] = []
        seen: set[str] = set()
        for key, catalog in self._catalogs.items():
            _session_id, core_root, workspace, catalog_fingerprint = key
            if core_root != str(core.root) or workspace != str(self.workspace) or catalog_fingerprint != fingerprint:
                continue
            for tool in catalog.tools:
                if tool.name in seen:
                    continue
                seen.add(tool.name)
                tools.append(tool)
        return sorted(tools, key=lambda tool: tool.name)

    def tool_info(self, name: str) -> McpToolInfo | None:
        return self._tool_index.get(name)

    async def call_tool(self, tool: McpToolInfo, arguments: dict[str, Any]) -> ToolResult:
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

    async def close(self) -> None:
        async with self._lock:
            connections = list(self._connections.values())
            self._connections.clear()
            self._catalogs.clear()
            self._tool_index.clear()
        await asyncio.gather(*(connection.close() for connection in connections), return_exceptions=True)

    async def _build_catalog(
        self,
        core: LoadedCore,
        *,
        catalog_key: tuple[str, str, str, str],
        fingerprint: str,
        emit_event: EventEmitter | None,
    ) -> McpCatalog:
        catalog = McpCatalog(fingerprint=fingerprint)
        reserved_names: set[str] = set()
        used_server_names: set[str] = set()
        for server in core.mcp_servers:
            if not server.enabled:
                continue
            safe_server_name = sanitize_server_name(server.server_id, used_server_names)
            try:
                env = interpolate_env_map(server.manifest.env)
                headers = interpolate_env_map(server.manifest.headers)
            except KeyError as exc:
                message = f"missing environment variable: {exc.args[0]}"
                self._diagnose(catalog, server, message, emit_event=emit_event)
                continue
            connection: McpClientConnection | None = None
            try:
                connection = self.client_factory(server, env, headers, self.workspace, self._stderr_log_path())
                tools = await connection.list_tools()
            except Exception as exc:
                if connection is not None:
                    with contextlib.suppress(Exception):
                        await connection.close()
                self._diagnose(catalog, server, str(exc), emit_event=emit_event)
                continue
            connection_key = (*catalog_key, server.server_id)
            self._connections[connection_key] = connection
            for listed_tool in tools:
                tool_name = str(getattr(listed_tool, "name", "") or "").strip()
                if not tool_name or not self._tool_selected(server, tool_name):
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
                        description=self._tool_description(server, listed_tool),
                        input_schema=self._tool_schema(listed_tool),
                        relative_path=server.relative_path,
                        risk=server.manifest.risk,
                        approval_policy=server.manifest.approval_policy,
                        capability=server.capability,
                        timeout_seconds=server.manifest.timeout_seconds,
                        connection_key=connection_key,
                    )
                )
        return catalog

    def _connection_for_tool(self, tool: McpToolInfo) -> McpClientConnection | None:
        return self._connections.get(tool.connection_key)

    def _diagnose(
        self,
        catalog: McpCatalog,
        server: McpServerDefinition,
        message: str,
        *,
        emit_event: EventEmitter | None,
    ) -> None:
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
            {
                "server_id": server.server_id,
                "relative_path": server.relative_path,
                "raw_manifest": server.raw_manifest,
            }
            for server in core.mcp_servers
        ]
        text = json.dumps(payload, ensure_ascii=True, sort_keys=True, default=str)
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

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
    ) -> None:
        self.server = server
        self.env = env
        self.headers = headers
        self.workspace = workspace
        self.stderr_log_path = stderr_log_path
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
            from mcp.client.streamable_http import streamablehttp_client
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
                read_stream, write_stream, _get_session_id = await stack.enter_async_context(
                    streamablehttp_client(
                        str(self.server.manifest.url),
                        headers=self.headers or None,
                        timeout=self.server.manifest.connect_timeout_seconds,
                        sse_read_timeout=max(self.server.manifest.timeout_seconds, 60),
                    )
                )
            session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
            await asyncio.wait_for(session.initialize(), timeout=self.server.manifest.connect_timeout_seconds)
            self._session = session
            return session
        except Exception:
            await self.close()
            raise

    def _cwd(self) -> Path | None:
        cwd = self.server.manifest.cwd
        if not cwd:
            return None
        path = Path(cwd).expanduser()
        if not path.is_absolute():
            path = self.workspace / path
        return path.resolve()

    def _stdio_env(self) -> dict[str, str] | None:
        if not self.env:
            return None
        merged = {
            key: value
            for key, value in os.environ.items()
            if key in {"PATH", "HOME", "USER", "LANG", "LC_ALL", "TERM", "SHELL", "TMPDIR"}
        }
        merged.update(self.env)
        return merged


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
