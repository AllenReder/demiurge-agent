from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

import pytest
import yaml

from demiurge.app import create_app
from demiurge.core import McpServerDefinition
from demiurge.mcp.runtime import DefaultMcpClientConnection, McpRuntimeError
from demiurge.providers import ToolCall
from demiurge.runtime.scope import PrincipalScopeResolver
from demiurge.sdk import AgentInput, TurnContext
from demiurge.security.approval import ApprovalDecision, StaticApprovalProvider
from demiurge.security.capabilities import CapabilityFacade
from demiurge.tools.registry import ToolRegistryCollisionError


@dataclass(slots=True)
class FakeListedTool:
    name: str
    description: str = ""
    inputSchema: dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}})


@dataclass(slots=True)
class FakeContentBlock:
    type: str
    text: str = ""


@dataclass(slots=True)
class FakeMcpResult:
    content: list[Any] = field(default_factory=list)
    structuredContent: Any | None = None
    isError: bool = False


class FakeMcpConnection:
    def __init__(self, tools: list[FakeListedTool], result: FakeMcpResult | None = None):
        self.tools = tools
        self.result = result or FakeMcpResult(content=[FakeContentBlock(type="text", text="ok")])
        self.list_calls = 0
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.closed = False

    async def list_tools(self):
        self.list_calls += 1
        return self.tools

    async def call_tool(self, name, arguments, *, timeout_seconds):
        self.calls.append((name, dict(arguments)))
        return self.result

    async def close(self):
        self.closed = True


class HangingMcpConnection(FakeMcpConnection):
    def __init__(self):
        super().__init__([])
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def list_tools(self):
        self.list_calls += 1
        self.started.set()
        await self.release.wait()
        return []


class SignalingMcpConnection(FakeMcpConnection):
    def __init__(self, tools: list[FakeListedTool]):
        super().__init__(tools)
        self.started = asyncio.Event()

    async def list_tools(self):
        self.started.set()
        return await super().list_tools()


class SequenceApprovalProvider:
    name = "sequence"

    def __init__(self, decisions: list[str]):
        self.decisions = list(decisions)
        self.requests = []

    def decide(self, request):
        self.requests.append(request)
        return ApprovalDecision(
            self.decisions.pop(0),
            "sequence approval",
        )


class ServerApprovalProvider:
    name = "server"

    def __init__(self, decisions: dict[str, str]):
        self.decisions = dict(decisions)
        self.requests = []

    def decide(self, request):
        self.requests.append(request)
        server_id = request.tool_name.removeprefix("mcp.connect:")
        return ApprovalDecision(
            self.decisions[server_id],
            f"{server_id} approval",
        )


def _turn(core):
    return TurnContext(
        session_id="session_test",
        turn_id="turn_test",
        core_id=core.core_id,
        core_revision=core.revision,
        user_input=AgentInput(content="test"),
    )


def _write_mcp_server(app, server_id: str, content: str) -> None:
    mcp_dir = app.version_store.active_core_path("assistant") / "agent" / "mcp"
    mcp_dir.mkdir(parents=True, exist_ok=True)
    (mcp_dir / f"{server_id}.yaml").write_text(content, encoding="utf-8")
    _set_capabilities(app, {f"mcp.connect:{server_id}": {}})
    app.approval_runtime.provider = StaticApprovalProvider("allow")


def _set_capabilities(app, capabilities: dict[str, dict]) -> None:
    manifest_path = app.version_store.active_core_path("assistant") / "agent.yaml"
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    raw.setdefault("capabilities", {}).setdefault("defaults", {}).update(capabilities)
    manifest_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")


def _principal_scope(app, core, turn):
    resolver = PrincipalScopeResolver(app.runtime_store)
    if not app.runtime_store.session_owner_exists(turn.session_id):
        issued = resolver.local_operator(
            active_session_id=turn.session_id,
            reason="bind direct MCP tool test session",
            allow_unowned_active=True,
        )
        app.session_runtime.create_session(
            session_id=turn.session_id,
            core_id=core.core_id,
            core_revision=core.revision,
            principal_scope=issued,
        )
    return resolver.origin_scope(session_id=turn.session_id)


async def _prepare(app):
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    turn = _turn(core)
    await app.tool_runtime.prepare_for_turn(
        core,
        turn,
        capability=CapabilityFacade(core),
        principal_scope=_principal_scope(app, core, turn),
        emit_event=app.runner.event_log.emit,
    )
    return core


async def _prepare_turn(app, core, turn):
    await app.tool_runtime.prepare_for_turn(
        core,
        turn,
        capability=CapabilityFacade(core),
        principal_scope=_principal_scope(app, core, turn),
        emit_event=app.runner.event_log.emit,
    )


def _execute(app, core, name, arguments):
    turn = _turn(core)
    return app.runner.execute_call(
        ToolCall(name=name, arguments=arguments, id=f"call_{name}"),
        core=core,
        turn=turn,
        capability=CapabilityFacade(core),
        principal_scope=_principal_scope(app, core, turn),
        emit_event=app.runner.event_log.emit,
    )


def test_mcp_stdio_environment_uses_shared_allowlist(monkeypatch, tmp_path):
    variable = "DEMIURGE_MCP_SYNTHETIC_PROVIDER_SECRET"
    sentinel = "SYNTHETIC_MCP_PROVIDER_SECRET"
    monkeypatch.setenv(variable, sentinel)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = create_app(home=tmp_path / "home", provider_name="fake", workspace=workspace)
    _write_mcp_server(
        app,
        "docs",
        "transport: stdio\ncommand: fake-mcp\napproval_policy: auto\n",
    )
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    connection = DefaultMcpClientConnection(
        core.mcp_servers[0],
        {"DECLARED_SETTING": "visible"},
        {},
        workspace,
        tmp_path / "home/logs/mcp-stderr.log",
    )

    env = connection._stdio_env()

    assert env["DECLARED_SETTING"] == "visible"
    assert variable not in env
    assert sentinel not in env.values()
    assert env["HOME"] == str((tmp_path / "home/mcp-home").resolve())
    assert env["PATH"]


@pytest.mark.asyncio
async def test_mcp_discovery_adds_sanitized_filtered_tool(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    _write_mcp_server(
        app,
        "docs server",
        "transport: stdio\n"
        "command: fake-mcp\n"
        "approval_policy: auto\n"
        "tools:\n"
        "  include:\n"
        "    - search*\n",
    )
    connection = FakeMcpConnection(
        [
            FakeListedTool(
                name="search docs",
                description="Search documentation.",
                inputSchema={"type": "object", "properties": {"query": {"type": "string"}}},
            ),
            FakeListedTool(name="delete docs"),
        ]
    )
    app.tool_runtime.mcp_runtime.client_factory = lambda *_args: connection

    core = await _prepare(app)

    definitions = app.tool_runtime.definitions_for(core)
    assert [tool.name for tool in definitions if tool.name.startswith("docs-server__")] == ["docs-server__search-docs"]
    tools_list = await _execute(app, core, "tools_list", {})
    assert "docs-server__search-docs" in tools_list.content
    assert "delete docs" not in tools_list.content


@pytest.mark.asyncio
async def test_resolved_mcp_entry_is_bound_to_the_current_turn_session(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    _write_mcp_server(
        app,
        "docs",
        "transport: stdio\n"
        "command: fake-mcp\n"
        "approval_policy: auto\n",
    )
    _set_capabilities(app, {"mcp.call:docs": {}})
    connection_a = FakeMcpConnection([FakeListedTool(name="lookup")])
    connection_b = FakeMcpConnection([FakeListedTool(name="lookup")])
    connections = [connection_a, connection_b]
    app.tool_runtime.mcp_runtime.client_factory = lambda *_args: connections.pop(0)
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    turn_a = TurnContext(
        session_id="session_A",
        turn_id="turn_A",
        core_id=core.core_id,
        core_revision=core.revision,
        user_input=AgentInput(content="A"),
    )
    turn_b = TurnContext(
        session_id="session_B",
        turn_id="turn_B",
        core_id=core.core_id,
        core_revision=core.revision,
        user_input=AgentInput(content="B"),
    )
    await _prepare_turn(app, core, turn_a)
    await _prepare_turn(app, core, turn_b)

    catalog_a = app.tool_runtime.resolve_effects(core, turn=turn_a)
    catalog_b = app.tool_runtime.resolve_effects(core, turn=turn_b)
    entry_a = catalog_a.entry_for("docs__lookup")
    entry_b = catalog_b.entry_for("docs__lookup")

    assert entry_a is not None
    assert entry_b is not None
    assert entry_a.adapter_key.startswith("mcp:session_A:")
    assert entry_b.adapter_key.startswith("mcp:session_B:")
    request_a = catalog_a.request_for(
        ToolCall(name="docs__lookup", arguments={"origin": "A"}, id="call_A")
    )
    assert request_a is not None
    effect_result = await app.tool_runtime.execute(
        request_a,
        core=core,
        turn=turn_a,
        capability=CapabilityFacade(core),
        principal_scope=_principal_scope(app, core, turn_a),
    )

    assert effect_result.status == "succeeded"
    result = effect_result.to_tool_result()
    assert result.is_error is False
    assert connection_a.calls == [("lookup", {"origin": "A"})]
    assert connection_b.calls == []

    replayed = await app.tool_runtime.execute(
        request_a,
        core=core,
        turn=turn_b,
        capability=CapabilityFacade(core),
        principal_scope=_principal_scope(app, core, turn_b),
    )

    assert replayed.status == "invalid"
    assert replayed.error is not None
    assert replayed.error.execution_started is False
    assert "MCP effect entry does not match" in replayed.result.content
    assert connection_a.calls == [("lookup", {"origin": "A"})]
    assert connection_b.calls == []
    await app.close()


@pytest.mark.asyncio
async def test_final_registry_rejects_authored_mcp_name_collision(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    tool_root = (
        app.version_store.active_core_path("assistant")
        / "agent"
        / "tools"
        / "docs__lookup"
    )
    tool_root.mkdir(parents=True)
    (tool_root / "tool.yaml").write_text(
        "entrypoint: module:run\n"
        "description: Authored collision probe.\n"
        "input_schema:\n"
        "  type: object\n"
        "  properties: {}\n"
        "capabilities: []\n",
        encoding="utf-8",
    )
    (tool_root / "module.py").write_text(
        "def run(ctx, arguments):\n"
        "    return {'content': 'authored'}\n",
        encoding="utf-8",
    )
    _write_mcp_server(
        app,
        "docs",
        "transport: stdio\ncommand: fake-mcp\napproval_policy: auto\n",
    )
    app.tool_runtime.mcp_runtime.client_factory = lambda *_args: FakeMcpConnection(
        [FakeListedTool(name="lookup")]
    )
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    turn = _turn(core)
    await _prepare_turn(app, core, turn)

    with pytest.raises(ToolRegistryCollisionError) as exc_info:
        app.tool_runtime.resolve_effects(core, turn=turn)

    message = str(exc_info.value)
    assert "tool name collision: docs__lookup" in message
    assert "authored:agent/tools/docs__lookup" in message
    assert "mcp:agent/mcp/docs.yaml:docs/lookup" in message
    await app.close()


@pytest.mark.asyncio
async def test_mcp_missing_capability_denies_before_server_call(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    _write_mcp_server(
        app,
        "docs",
        "transport: stdio\n"
        "command: fake-mcp\n"
        "approval_policy: auto\n",
    )
    connection = FakeMcpConnection([FakeListedTool(name="lookup")])
    app.tool_runtime.mcp_runtime.client_factory = lambda *_args: connection
    core = await _prepare(app)

    result = await _execute(app, core, "docs__lookup", {"q": "x"})

    assert result.is_error is True
    assert "capability denied" in result.content
    assert connection.calls == []


@pytest.mark.asyncio
async def test_mcp_approval_denial_prevents_server_call(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    _set_capabilities(app, {"mcp.call:docs": {}})
    _write_mcp_server(
        app,
        "docs",
        "transport: stdio\n"
        "command: fake-mcp\n"
        "approval_policy: prompt\n",
    )
    app.approval_runtime.provider = SequenceApprovalProvider(["allow", "deny"])
    connection = FakeMcpConnection([FakeListedTool(name="lookup")])
    app.tool_runtime.mcp_runtime.client_factory = lambda *_args: connection
    core = await _prepare(app)

    result = await _execute(app, core, "docs__lookup", {"q": "x"})

    assert result.is_error is True
    assert "approval denied" in result.content
    assert connection.calls == []
    approval = next(
        event
        for event in reversed(app.runner.event_log.tail(20))
        if event["type"] == "approval.decided"
        and event["action"] == "mcp.call"
    )
    assert approval["capability"] == "mcp.call:docs"
    assert approval["decision"] == "deny"


@pytest.mark.asyncio
async def test_mcp_connect_denial_prevents_factory_and_discovery(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    _set_capabilities(app, {"mcp.connect:docs": {}})
    _write_mcp_server(
        app,
        "docs",
        "transport: stdio\n"
        "command: fake-mcp\n"
        "approval_policy: prompt\n",
    )
    app.approval_runtime.provider = StaticApprovalProvider("deny")
    connection = FakeMcpConnection([FakeListedTool(name="lookup")])
    factory_calls = []

    def factory(*args):
        factory_calls.append(args)
        return connection

    app.tool_runtime.mcp_runtime.client_factory = factory

    await app.runner.run_turn("hello")

    assert factory_calls == []
    assert connection.list_calls == 0
    approval = next(
        event
        for event in reversed(app.runner.event_log.tail(20))
        if event["type"] == "approval.decided"
    )
    assert approval["capability"] == "mcp.connect:docs"
    assert approval["action"] == "mcp.connect"
    assert approval["decision"] == "deny"
    await app.close()


@pytest.mark.asyncio
async def test_mcp_auto_manifest_cannot_bypass_host_connect_trust_prompt(
    tmp_path,
):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    _write_mcp_server(
        app,
        "docs",
        "transport: stdio\n"
        "command: fake-docs\n"
        "approval_policy: auto\n",
    )
    app.approval_runtime.provider = StaticApprovalProvider("deny")
    factory_calls = 0

    def factory(*_args):
        nonlocal factory_calls
        factory_calls += 1
        return FakeMcpConnection([FakeListedTool(name="lookup")])

    app.tool_runtime.mcp_runtime.client_factory = factory

    await app.runner.run_turn("hello")

    assert factory_calls == 0
    approval = next(
        event
        for event in reversed(app.runner.event_log.tail(20))
        if event["type"] == "approval.decided"
        and event["action"] == "mcp.connect"
    )
    assert approval["policy"] == "prompt"
    assert approval["decision"] == "deny"
    await app.close()


@pytest.mark.asyncio
async def test_low_level_mcp_prepare_fails_closed_without_connect_authority(
    tmp_path,
):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    _write_mcp_server(
        app,
        "docs",
        "transport: stdio\n"
        "command: fake-docs\n"
        "approval_policy: auto\n",
    )
    factory_calls = 0

    def factory(*_args):
        nonlocal factory_calls
        factory_calls += 1
        return FakeMcpConnection([FakeListedTool(name="lookup")])

    app.tool_runtime.mcp_runtime.client_factory = factory
    core = app.core_loader.load(
        app.version_store.active_core_path("assistant")
    )

    with pytest.raises(McpRuntimeError, match="connect authority"):
        await app.tool_runtime.mcp_runtime.prepare_for_turn(
            core,
            _turn(core),
        )

    assert factory_calls == 0
    await app.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["npx", "uvx"])
async def test_mcp_package_runner_denial_prevents_factory(
    tmp_path,
    command,
):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    _write_mcp_server(
        app,
        "packages",
        "transport: stdio\n"
        f"command: {command}\n"
        "args:\n"
        "  - synthetic-package\n"
        "approval_policy: prompt\n",
    )
    app.approval_runtime.provider = StaticApprovalProvider("deny")
    factory_calls = 0

    def factory(*_args):
        nonlocal factory_calls
        factory_calls += 1
        return FakeMcpConnection([FakeListedTool(name="lookup")])

    app.tool_runtime.mcp_runtime.client_factory = factory

    await app.runner.run_turn("hello")

    assert factory_calls == 0
    await app.close()


@pytest.mark.asyncio
async def test_mcp_http_connect_denial_prevents_client_and_socket_setup(
    tmp_path,
):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    _write_mcp_server(
        app,
        "remote",
        "transport: streamable_http\n"
        "url: https://example.test/mcp\n"
        "approval_policy: prompt\n",
    )
    app.approval_runtime.provider = StaticApprovalProvider("deny")
    factory_calls = 0

    def factory(*_args):
        nonlocal factory_calls
        factory_calls += 1
        return FakeMcpConnection([FakeListedTool(name="lookup")])

    app.tool_runtime.mcp_runtime.client_factory = factory

    await app.runner.run_turn("hello")

    assert factory_calls == 0
    await app.close()


@pytest.mark.asyncio
async def test_mcp_connect_approval_preview_is_auditable_without_secret_values(
    tmp_path,
):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    _write_mcp_server(
        app,
        "local",
        "transport: stdio\n"
        "command: npx\n"
        "args:\n"
        "  - --yes\n"
        "  - synthetic-package\n"
        "  - --token\n"
        "  - SYNTHETIC_ARG_SECRET\n"
        "cwd: .\n"
        "env:\n"
        "  MCP_TOKEN: SYNTHETIC_ENV_SECRET\n"
        "approval_policy: prompt\n",
    )
    _write_mcp_server(
        app,
        "remote",
        "transport: streamable_http\n"
        "url: 'https://user:SYNTHETIC_PASSWORD@example.test/mcp/SYNTHETIC_PATH_SECRET/%53YNTHETIC_ENCODED_PATH_SECRET?token=SYNTHETIC_QUERY_SECRET&mode=read'\n"
        "headers:\n"
        "  Authorization: Bearer SYNTHETIC_HEADER_SECRET\n"
        "  X-Tenant: visible-name-only\n"
        "approval_policy: prompt\n",
    )
    provider = ServerApprovalProvider(
        {"local": "deny", "remote": "deny"}
    )
    app.approval_runtime.provider = provider

    await app.runner.run_turn("hello")

    requests = {request.tool_name: request for request in provider.requests}
    local = requests["mcp.connect:local"].arguments_preview
    remote = requests["mcp.connect:remote"].arguments_preview
    serialized = json.dumps(
        {"local": local, "remote": remote},
        ensure_ascii=False,
        sort_keys=True,
    )
    assert local["command"] == "npx"
    assert local["cwd"] == "."
    assert local["env_names"] == ["MCP_TOKEN"]
    assert local["args"][0] == "--yes"
    assert local["args"][2] == "--token=<redacted>"
    assert remote["url"].startswith(
        "https://example.test/<value sha256:"
    )
    assert remote["url"].endswith(
        "?mode=<redacted>&token=<redacted>"
    )
    assert remote["header_names"] == ["Authorization", "X-Tenant"]
    for secret in (
        "SYNTHETIC_ARG_SECRET",
        "SYNTHETIC_ENV_SECRET",
        "SYNTHETIC_PASSWORD",
        "SYNTHETIC_QUERY_SECRET",
        "SYNTHETIC_HEADER_SECRET",
        "SYNTHETIC_PATH_SECRET",
        "SYNTHETIC_ENCODED_PATH_SECRET",
        "visible-name-only",
    ):
        assert secret not in serialized
    await app.close()


@pytest.mark.asyncio
async def test_mcp_connect_capability_denial_precedes_approval_and_factory(
    tmp_path,
):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    _write_mcp_server(
        app,
        "docs",
        "transport: stdio\n"
        "command: fake-mcp\n"
        "approval_policy: prompt\n",
    )
    manifest_path = app.version_store.active_core_path("assistant") / "agent.yaml"
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    raw["capabilities"]["defaults"].pop("mcp.connect:docs")
    manifest_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    app.approval_runtime.provider = StaticApprovalProvider("allow")
    connection = FakeMcpConnection([FakeListedTool(name="lookup")])
    factory_calls = []

    def factory(*args):
        factory_calls.append(args)
        return connection

    app.tool_runtime.mcp_runtime.client_factory = factory

    await app.runner.run_turn("hello")

    assert factory_calls == []
    assert connection.list_calls == 0
    events = app.runner.event_log.tail(20)
    assert all(event["type"] != "approval.requested" for event in events)
    denial = next(
        event for event in reversed(events) if event["type"] == "mcp.server_denied"
    )
    assert denial["capability"] == "mcp.connect:docs"
    assert "capability denied" in denial["reason"]
    await app.close()


@pytest.mark.asyncio
async def test_mcp_connect_rejects_cwd_escape_before_approval_and_factory(
    tmp_path,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    app = create_app(
        home=tmp_path / "home",
        provider_name="fake",
        workspace=workspace,
    )
    _write_mcp_server(
        app,
        "docs",
        "transport: stdio\n"
        "command: fake-mcp\n"
        f"cwd: {outside}\n"
        "approval_policy: prompt\n",
    )
    app.approval_runtime.provider = StaticApprovalProvider("allow")
    connection = FakeMcpConnection([FakeListedTool(name="lookup")])
    factory_calls = []

    def factory(*args):
        factory_calls.append(args)
        return connection

    app.tool_runtime.mcp_runtime.client_factory = factory

    await app.runner.run_turn("hello")

    assert factory_calls == []
    assert connection.list_calls == 0
    events = app.runner.event_log.tail(20)
    assert all(event["type"] != "approval.requested" for event in events)
    denial = next(
        event for event in reversed(events) if event["type"] == "mcp.server_denied"
    )
    assert denial["capability"] == "mcp.connect:docs"
    assert "path escapes workspace" in denial["reason"]
    await app.close()


@pytest.mark.asyncio
async def test_mcp_stdio_without_configured_cwd_uses_host_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = create_app(
        home=tmp_path / "home",
        provider_name="fake",
        workspace=workspace,
    )
    _write_mcp_server(
        app,
        "docs",
        "transport: stdio\n"
        "command: fake-mcp\n"
        "approval_policy: prompt\n",
    )
    core = app.core_loader.load(
        app.version_store.active_core_path("assistant")
    )
    connection = DefaultMcpClientConnection(
        core.mcp_servers[0],
        {},
        {},
        workspace,
        tmp_path / "mcp.log",
    )

    assert connection._cwd() == workspace.resolve()
    await app.close()


@pytest.mark.asyncio
async def test_mcp_connect_denial_is_rechecked_on_the_next_turn(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    _write_mcp_server(
        app,
        "docs",
        "transport: stdio\n"
        "command: fake-mcp\n"
        "approval_policy: prompt\n",
    )
    app.approval_runtime.provider = SequenceApprovalProvider(["deny", "allow"])
    connection = FakeMcpConnection([FakeListedTool(name="lookup")])
    factory_calls = []

    def factory(*args):
        factory_calls.append(args)
        return connection

    app.tool_runtime.mcp_runtime.client_factory = factory

    await app.runner.run_turn("first")
    await app.runner.run_turn("second")

    assert len(factory_calls) == 1
    assert connection.list_calls == 1
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    active_revision = app.version_store.active_pointer(
        "assistant"
    ).active_revision
    assert app.tool_runtime.resolve_effects(
        core,
        turn=TurnContext(
            session_id=app.runner.session_id,
            turn_id="turn_after_reapproval",
            core_id=core.core_id,
            core_revision=active_revision,
            user_input=AgentInput(content="inspect"),
        ),
    ).entry_for("docs__lookup") is not None
    await app.close()


@pytest.mark.asyncio
async def test_mcp_success_prefers_structured_content(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    _set_capabilities(app, {"mcp.call:docs": {}})
    _write_mcp_server(
        app,
        "docs",
        "transport: stdio\n"
        "command: fake-mcp\n"
        "approval_policy: auto\n",
    )
    connection = FakeMcpConnection(
        [FakeListedTool(name="lookup")],
        result=FakeMcpResult(
            content=[FakeContentBlock(type="text", text="fallback text")],
            structuredContent={"answer": "structured"},
        ),
    )
    app.tool_runtime.mcp_runtime.client_factory = lambda *_args: connection
    core = await _prepare(app)

    result = await _execute(app, core, "docs__lookup", {"q": "x"})

    assert result.is_error is False
    assert '"answer": "structured"' in result.content
    assert result.data["mcpServer"] == "docs"
    assert result.data["mcpTool"] == "lookup"
    assert connection.calls == [("lookup", {"q": "x"})]


@pytest.mark.asyncio
async def test_mcp_02_tool_dispatch_stays_bound_to_originating_session_connection(tmp_path):
    """MCP-02: dispatch must use the connection resolved for the originating session."""
    app = create_app(home=tmp_path / "home", provider_name="fake")
    _set_capabilities(app, {"mcp.call:docs": {}})
    _write_mcp_server(
        app,
        "docs",
        "transport: stdio\n"
        "command: fake-mcp\n"
        "approval_policy: auto\n",
    )
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    turn_a = TurnContext(
        session_id="session_A",
        turn_id="turn_A",
        core_id=core.core_id,
        core_revision=core.revision,
        user_input=AgentInput(content="probe A"),
    )
    turn_b = TurnContext(
        session_id="session_B",
        turn_id="turn_B",
        core_id=core.core_id,
        core_revision=core.revision,
        user_input=AgentInput(content="probe B"),
    )
    resolver = PrincipalScopeResolver(app.runtime_store)
    issued_a = resolver.local_operator(
        active_session_id=turn_a.session_id,
        reason="bind MCP origin session A",
        allow_unowned_active=True,
    )
    app.session_runtime.create_session(
        session_id=turn_a.session_id,
        core_id=core.core_id,
        core_revision=core.revision,
        principal_scope=issued_a,
    )
    scope_a = resolver.origin_scope(session_id=turn_a.session_id)
    connection_a = FakeMcpConnection([FakeListedTool(name="lookup")])
    connection_b = FakeMcpConnection([FakeListedTool(name="lookup")])
    connections = iter([connection_a, connection_b])
    app.tool_runtime.mcp_runtime.client_factory = lambda *_args: next(connections)

    await _prepare_turn(app, core, turn_a)
    await _prepare_turn(app, core, turn_b)
    catalog_a = app.tool_runtime.resolve_effects(core, turn=turn_a)
    request_a = catalog_a.request_for(
        ToolCall(name="docs__lookup", arguments={"origin": "session_A"}, id="call_A")
    )
    assert request_a is not None
    effect_result = await app.tool_runtime.execute(
        request_a,
        core=core,
        turn=turn_a,
        capability=CapabilityFacade(core),
        principal_scope=scope_a,
        emit_event=app.runner.event_log.emit,
    )
    result = effect_result.to_tool_result()

    assert result.is_error is False
    assert {"session_A": connection_a.calls, "session_B": connection_b.calls} == {
        "session_A": [("lookup", {"origin": "session_A"})],
        "session_B": [],
    }


@pytest.mark.asyncio
async def test_mcp_connection_failure_emits_diagnostic_without_breaking_builtin_tools(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    _write_mcp_server(
        app,
        "broken",
        "transport: stdio\n"
        "command: fake-mcp\n"
        "approval_policy: auto\n",
    )

    def failing_factory(server: McpServerDefinition, *_args):
        raise RuntimeError(f"cannot start {server.server_id}")

    app.tool_runtime.mcp_runtime.client_factory = failing_factory
    core = await _prepare(app)

    assert all(not tool.name.startswith("broken__") for tool in app.tool_runtime.definitions_for(core))
    tools_list = await _execute(app, core, "tools_list", {})
    assert tools_list.is_error is False
    event = next(item for item in app.runner.event_log.tail(20) if item["type"] == "mcp.server_failed")
    assert event["server_id"] == "broken"


@pytest.mark.asyncio
async def test_mcp_missing_env_reference_skips_server_before_factory(tmp_path, monkeypatch):
    monkeypatch.delenv("DEMIURGE_TEST_MISSING_MCP_TOKEN", raising=False)
    app = create_app(home=tmp_path / "home", provider_name="fake")
    _write_mcp_server(
        app,
        "remote",
        "transport: streamable_http\n"
        "url: https://example.test/mcp\n"
        "approval_policy: auto\n"
        "headers:\n"
        "  Authorization: Bearer ${DEMIURGE_TEST_MISSING_MCP_TOKEN}\n",
    )
    called = False

    def factory(*_args):
        nonlocal called
        called = True
        return FakeMcpConnection([FakeListedTool(name="lookup")])

    app.tool_runtime.mcp_runtime.client_factory = factory
    core = await _prepare(app)

    assert called is False
    assert all(not tool.name.startswith("remote__") for tool in app.tool_runtime.definitions_for(core))
    event = next(item for item in app.runner.event_log.tail(20) if item["type"] == "mcp.server_failed")
    assert event["server_id"] == "remote"
    assert "missing environment variable" in event["message"]


@pytest.mark.asyncio
async def test_mcp_03_discovery_timeout_does_not_block_healthy_server(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    _write_mcp_server(
        app,
        "alpha",
        "transport: stdio\n"
        "command: fake-alpha\n"
        "approval_policy: auto\n"
        "connect_timeout_seconds: 0.05\n",
    )
    _write_mcp_server(
        app,
        "beta",
        "transport: stdio\n"
        "command: fake-beta\n"
        "approval_policy: auto\n",
    )
    alpha = HangingMcpConnection()
    beta = FakeMcpConnection([FakeListedTool(name="lookup")])

    def factory(server, *_args):
        return alpha if server.server_id == "alpha" else beta

    app.tool_runtime.mcp_runtime.client_factory = factory
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    turn = _turn(core)

    await asyncio.wait_for(_prepare_turn(app, core, turn), timeout=0.5)

    assert alpha.list_calls == 1
    assert alpha.closed is True
    assert beta.list_calls == 1
    assert app.tool_runtime.resolve_effects(core, turn=turn).entry_for(
        "beta__lookup"
    ) is not None
    event = next(
        item
        for item in app.runner.event_log.tail(20)
        if item["type"] == "mcp.server_failed" and item["server_id"] == "alpha"
    )
    assert "timed out" in event["message"].lower()
    await app.close()


@pytest.mark.asyncio
async def test_mcp_03_discovery_starts_servers_with_bounded_concurrency(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    _write_mcp_server(
        app,
        "alpha",
        "transport: stdio\n"
        "command: fake-alpha\n"
        "approval_policy: auto\n"
        "connect_timeout_seconds: 1\n",
    )
    _write_mcp_server(
        app,
        "beta",
        "transport: stdio\n"
        "command: fake-beta\n"
        "approval_policy: auto\n",
    )
    alpha = HangingMcpConnection()
    beta = SignalingMcpConnection([FakeListedTool(name="lookup")])

    def factory(server, *_args):
        return alpha if server.server_id == "alpha" else beta

    app.tool_runtime.mcp_runtime.client_factory = factory
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    turn = _turn(core)
    prepare = asyncio.create_task(_prepare_turn(app, core, turn))
    try:
        await asyncio.wait_for(alpha.started.wait(), timeout=0.2)
        await asyncio.wait_for(beta.started.wait(), timeout=0.2)
    finally:
        alpha.release.set()
        await prepare

    assert app.tool_runtime.resolve_effects(core, turn=turn).entry_for(
        "beta__lookup"
    ) is not None
    await app.close()


@pytest.mark.asyncio
async def test_mcp_03_hung_discovery_does_not_block_another_session_turn(
    tmp_path,
):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    _write_mcp_server(
        app,
        "docs",
        "transport: stdio\n"
        "command: fake-alpha\n"
        "approval_policy: auto\n"
        "connect_timeout_seconds: 1\n",
    )
    core_alpha = app.core_loader.load(
        app.version_store.active_core_path("assistant")
    )
    turn_alpha = TurnContext(
        session_id="session_alpha",
        turn_id="turn_alpha",
        core_id=core_alpha.core_id,
        core_revision=core_alpha.revision,
        user_input=AgentInput(content="alpha"),
    )
    alpha = HangingMcpConnection()
    beta = SignalingMcpConnection([FakeListedTool(name="lookup")])

    def factory(server, *_args):
        return alpha if server.manifest.command == "fake-alpha" else beta

    app.tool_runtime.mcp_runtime.client_factory = factory
    prepare_alpha = asyncio.create_task(
        _prepare_turn(app, core_alpha, turn_alpha)
    )
    await asyncio.wait_for(alpha.started.wait(), timeout=0.2)

    _write_mcp_server(
        app,
        "docs",
        "transport: stdio\n"
        "command: fake-beta\n"
        "approval_policy: auto\n",
    )
    core_beta = app.core_loader.load(
        app.version_store.active_core_path("assistant")
    )
    turn_beta = TurnContext(
        session_id="session_beta",
        turn_id="turn_beta",
        core_id=core_beta.core_id,
        core_revision=core_beta.revision,
        user_input=AgentInput(content="beta"),
    )
    prepare_beta = asyncio.create_task(
        _prepare_turn(app, core_beta, turn_beta)
    )
    try:
        await asyncio.wait_for(beta.started.wait(), timeout=0.2)
    finally:
        alpha.release.set()
        await asyncio.gather(prepare_alpha, prepare_beta)

    assert app.tool_runtime.resolve_effects(
        core_beta,
        turn=turn_beta,
    ).entry_for("docs__lookup") is not None
    await app.close()


@pytest.mark.asyncio
async def test_mcp_discovery_concurrency_is_bounded_to_four_servers(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    connections: dict[str, HangingMcpConnection] = {}
    for index in range(5):
        server_id = f"server-{index}"
        _write_mcp_server(
            app,
            server_id,
            "transport: stdio\n"
            f"command: fake-{index}\n"
            "approval_policy: auto\n"
            "connect_timeout_seconds: 1\n",
        )
        connections[server_id] = HangingMcpConnection()
    app.tool_runtime.mcp_runtime.client_factory = (
        lambda server, *_args: connections[server.server_id]
    )
    core = app.core_loader.load(
        app.version_store.active_core_path("assistant")
    )
    turn = _turn(core)
    prepare = asyncio.create_task(_prepare_turn(app, core, turn))
    try:
        await asyncio.gather(
            *(
                asyncio.wait_for(
                    connections[f"server-{index}"].started.wait(),
                    timeout=0.2,
                )
                for index in range(4)
            )
        )
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(
                connections["server-4"].started.wait(),
                timeout=0.05,
            )
        connections["server-0"].release.set()
        await asyncio.wait_for(
            connections["server-4"].started.wait(),
            timeout=0.2,
        )
    finally:
        for connection in connections.values():
            connection.release.set()
        await prepare
    await app.close()


@pytest.mark.asyncio
async def test_mcp_discovery_concurrency_bound_is_shared_across_sessions(
    tmp_path,
):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    first_connections: dict[str, HangingMcpConnection] = {}
    second_connections: dict[str, HangingMcpConnection] = {}
    factory_calls: dict[str, int] = {}
    second_session_started = asyncio.Event()
    for index in range(4):
        server_id = f"server-{index}"
        _write_mcp_server(
            app,
            server_id,
            "transport: stdio\n"
            f"command: fake-{index}\n"
            "approval_policy: auto\n"
            "connect_timeout_seconds: 1\n",
        )
        first_connections[server_id] = HangingMcpConnection()
        second_connections[server_id] = HangingMcpConnection()

    def factory(server, *_args):
        call_count = factory_calls.get(server.server_id, 0)
        factory_calls[server.server_id] = call_count + 1
        if call_count == 0:
            return first_connections[server.server_id]
        connection = second_connections[server.server_id]
        connection.started = second_session_started
        return connection

    app.tool_runtime.mcp_runtime.client_factory = factory
    core = app.core_loader.load(
        app.version_store.active_core_path("assistant")
    )
    turn_a = TurnContext(
        session_id="session_A",
        turn_id="turn_A",
        core_id=core.core_id,
        core_revision=core.revision,
        user_input=AgentInput(content="A"),
    )
    turn_b = TurnContext(
        session_id="session_B",
        turn_id="turn_B",
        core_id=core.core_id,
        core_revision=core.revision,
        user_input=AgentInput(content="B"),
    )
    prepare_a = asyncio.create_task(_prepare_turn(app, core, turn_a))
    await asyncio.gather(
        *(
            asyncio.wait_for(connection.started.wait(), timeout=0.2)
            for connection in first_connections.values()
        )
    )
    prepare_b = asyncio.create_task(_prepare_turn(app, core, turn_b))
    try:
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(
                second_session_started.wait(),
                timeout=0.05,
            )
        first_connections["server-0"].release.set()
        await asyncio.wait_for(
            second_session_started.wait(),
            timeout=0.2,
        )
    finally:
        for connection in (
            *first_connections.values(),
            *second_connections.values(),
        ):
            connection.release.set()
        await asyncio.gather(prepare_a, prepare_b)
    await app.close()


@pytest.mark.asyncio
async def test_cancelled_mcp_discovery_closes_completed_and_active_connections(
    tmp_path,
):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    _write_mcp_server(
        app,
        "alpha",
        "transport: stdio\n"
        "command: fake-alpha\n"
        "approval_policy: auto\n"
        "connect_timeout_seconds: 1\n",
    )
    _write_mcp_server(
        app,
        "beta",
        "transport: stdio\n"
        "command: fake-beta\n"
        "approval_policy: auto\n"
        "connect_timeout_seconds: 1\n",
    )
    alpha = SignalingMcpConnection([FakeListedTool(name="lookup")])
    beta = HangingMcpConnection()
    app.tool_runtime.mcp_runtime.client_factory = (
        lambda server, *_args: alpha if server.server_id == "alpha" else beta
    )
    core = app.core_loader.load(
        app.version_store.active_core_path("assistant")
    )
    prepare = asyncio.create_task(_prepare_turn(app, core, _turn(core)))
    await asyncio.wait_for(alpha.started.wait(), timeout=0.2)
    await asyncio.wait_for(beta.started.wait(), timeout=0.2)

    prepare.cancel()
    with pytest.raises(asyncio.CancelledError):
        await prepare

    assert alpha.closed is True
    assert beta.closed is True
    await app.close()


@pytest.mark.asyncio
async def test_app_close_cancels_active_mcp_discovery_and_closes_connection(
    tmp_path,
):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    _write_mcp_server(
        app,
        "docs",
        "transport: stdio\n"
        "command: fake-docs\n"
        "approval_policy: auto\n"
        "connect_timeout_seconds: 10\n",
    )
    connection = HangingMcpConnection()
    app.tool_runtime.mcp_runtime.client_factory = lambda *_args: connection
    core = app.core_loader.load(
        app.version_store.active_core_path("assistant")
    )
    prepare = asyncio.create_task(_prepare_turn(app, core, _turn(core)))
    await asyncio.wait_for(connection.started.wait(), timeout=0.2)
    try:
        await asyncio.wait_for(app.close(), timeout=0.2)
        assert connection.closed is True
        with pytest.raises(asyncio.CancelledError):
            await prepare
    finally:
        if not prepare.done():
            prepare.cancel()
            await asyncio.gather(prepare, return_exceptions=True)


@pytest.mark.asyncio
async def test_mcp_03_failure_cache_retries_after_ttl(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    _write_mcp_server(
        app,
        "docs",
        "transport: stdio\n"
        "command: fake-docs\n"
        "approval_policy: auto\n",
    )
    now = [100.0]
    app.tool_runtime.mcp_runtime.failure_cache_ttl_seconds = 5.0
    app.tool_runtime.mcp_runtime._clock = lambda: now[0]
    connection = FakeMcpConnection([FakeListedTool(name="lookup")])
    factory_calls = 0

    def factory(*_args):
        nonlocal factory_calls
        factory_calls += 1
        if factory_calls == 1:
            raise RuntimeError("temporary discovery failure")
        return connection

    app.tool_runtime.mcp_runtime.client_factory = factory
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    turn = _turn(core)

    await _prepare_turn(app, core, turn)
    await _prepare_turn(app, core, turn)
    assert factory_calls == 1

    now[0] += 6.0
    await _prepare_turn(app, core, turn)

    assert factory_calls == 2
    assert app.tool_runtime.resolve_effects(core, turn=turn).entry_for(
        "docs__lookup"
    ) is not None
    await app.close()


@pytest.mark.asyncio
async def test_mcp_denied_server_recheck_keeps_healthy_peer_connection(
    tmp_path,
):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    for server_id in ("alpha", "beta"):
        _write_mcp_server(
            app,
            server_id,
            "transport: stdio\n"
            f"command: fake-{server_id}\n"
            "approval_policy: prompt\n",
        )
    provider = ServerApprovalProvider(
        {"alpha": "deny", "beta": "allow"}
    )
    app.approval_runtime.provider = provider
    beta = FakeMcpConnection([FakeListedTool(name="lookup")])
    factory_calls: list[str] = []

    def factory(server, *_args):
        factory_calls.append(server.server_id)
        assert server.server_id == "beta"
        return beta

    app.tool_runtime.mcp_runtime.client_factory = factory
    core = app.core_loader.load(
        app.version_store.active_core_path("assistant")
    )
    turn = _turn(core)

    await _prepare_turn(app, core, turn)
    await _prepare_turn(app, core, turn)

    assert factory_calls == ["beta"]
    assert beta.list_calls == 1
    assert beta.closed is False
    assert [
        request.tool_name for request in provider.requests
    ] == ["mcp.connect:alpha", "mcp.connect:beta", "mcp.connect:alpha"]
    assert app.tool_runtime.resolve_effects(
        core,
        turn=turn,
    ).entry_for("beta__lookup") is not None
    await app.close()
    assert beta.closed is True


@pytest.mark.asyncio
async def test_mcp_failed_server_ttl_retry_keeps_healthy_peer_connection(
    tmp_path,
):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    for server_id in ("alpha", "beta"):
        _write_mcp_server(
            app,
            server_id,
            "transport: stdio\n"
            f"command: fake-{server_id}\n"
            "approval_policy: auto\n",
        )
    now = [100.0]
    runtime = app.tool_runtime.mcp_runtime
    runtime.failure_cache_ttl_seconds = 5.0
    runtime._clock = lambda: now[0]
    alpha = FakeMcpConnection([FakeListedTool(name="lookup")])
    beta = FakeMcpConnection([FakeListedTool(name="lookup")])
    factory_calls: list[str] = []

    def factory(server, *_args):
        factory_calls.append(server.server_id)
        if server.server_id == "alpha" and factory_calls.count("alpha") == 1:
            raise RuntimeError("temporary alpha failure")
        return alpha if server.server_id == "alpha" else beta

    runtime.client_factory = factory
    core = app.core_loader.load(
        app.version_store.active_core_path("assistant")
    )
    turn = _turn(core)

    await _prepare_turn(app, core, turn)
    await _prepare_turn(app, core, turn)
    assert factory_calls.count("alpha") == 1
    assert factory_calls.count("beta") == 1

    now[0] += 6.0
    await _prepare_turn(app, core, turn)

    assert factory_calls.count("alpha") == 2
    assert factory_calls.count("beta") == 1
    assert alpha.list_calls == 1
    assert beta.list_calls == 1
    assert beta.closed is False
    catalog = app.tool_runtime.resolve_effects(core, turn=turn)
    assert catalog.entry_for("alpha__lookup") is not None
    assert catalog.entry_for("beta__lookup") is not None
    await app.close()
    assert alpha.closed is True
    assert beta.closed is True


@pytest.mark.asyncio
async def test_mcp_fingerprint_change_evicts_stale_session_connection(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    _write_mcp_server(
        app,
        "docs",
        "transport: stdio\n"
        "command: fake-docs-v1\n"
        "approval_policy: auto\n",
    )
    first = FakeMcpConnection([FakeListedTool(name="lookup")])
    second = FakeMcpConnection([FakeListedTool(name="lookup")])
    connections = iter([first, second])
    app.tool_runtime.mcp_runtime.client_factory = lambda *_args: next(connections)
    core_v1 = app.core_loader.load(app.version_store.active_core_path("assistant"))
    turn_v1 = _turn(core_v1)

    await _prepare_turn(app, core_v1, turn_v1)

    _write_mcp_server(
        app,
        "docs",
        "transport: stdio\n"
        "command: fake-docs-v2\n"
        "approval_policy: auto\n",
    )
    core_v2 = app.core_loader.load(app.version_store.active_core_path("assistant"))
    turn_v2 = _turn(core_v2)
    await _prepare_turn(app, core_v2, turn_v2)

    assert first.closed is True
    assert second.closed is False
    assert app.tool_runtime.resolve_effects(core_v2, turn=turn_v2).entry_for(
        "docs__lookup"
    ) is not None
    await app.close()
    assert second.closed is True


@pytest.mark.asyncio
async def test_mcp_server_fingerprint_change_preserves_healthy_peer_connection(
    tmp_path,
):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    for server_id in ("alpha", "beta"):
        _write_mcp_server(
            app,
            server_id,
            "transport: stdio\n"
            f"command: fake-{server_id}-v1\n"
            "approval_policy: auto\n",
        )
    alpha_v1 = FakeMcpConnection([FakeListedTool(name="lookup")])
    alpha_v2 = FakeMcpConnection([FakeListedTool(name="lookup")])
    beta = FakeMcpConnection([FakeListedTool(name="lookup")])
    factory_calls: list[tuple[str, str | None]] = []

    def factory(server, *_args):
        factory_calls.append((server.server_id, server.manifest.command))
        if server.server_id == "beta":
            return beta
        return alpha_v1 if server.manifest.command == "fake-alpha-v1" else alpha_v2

    app.tool_runtime.mcp_runtime.client_factory = factory
    core_v1 = app.core_loader.load(
        app.version_store.active_core_path("assistant")
    )
    turn_v1 = _turn(core_v1)
    await _prepare_turn(app, core_v1, turn_v1)

    _write_mcp_server(
        app,
        "alpha",
        "transport: stdio\n"
        "command: fake-alpha-v2\n"
        "approval_policy: auto\n",
    )
    core_v2 = app.core_loader.load(
        app.version_store.active_core_path("assistant")
    )
    turn_v2 = _turn(core_v2)
    await _prepare_turn(app, core_v2, turn_v2)

    assert factory_calls.count(("beta", "fake-beta-v1")) == 1
    assert beta.list_calls == 1
    assert beta.closed is False
    assert alpha_v1.closed is True
    assert alpha_v2.closed is False
    catalog = app.tool_runtime.resolve_effects(core_v2, turn=turn_v2)
    assert catalog.entry_for("alpha__lookup") is not None
    assert catalog.entry_for("beta__lookup") is not None
    await app.close()
    assert beta.closed is True
    assert alpha_v2.closed is True


@pytest.mark.asyncio
async def test_removing_all_mcp_declarations_closes_session_connections(
    tmp_path,
):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    _write_mcp_server(
        app,
        "docs",
        "transport: stdio\n"
        "command: fake-docs\n"
        "approval_policy: auto\n",
    )
    connection = FakeMcpConnection([FakeListedTool(name="lookup")])
    app.tool_runtime.mcp_runtime.client_factory = lambda *_args: connection
    core_with_mcp = app.core_loader.load(
        app.version_store.active_core_path("assistant")
    )
    turn = _turn(core_with_mcp)
    await _prepare_turn(app, core_with_mcp, turn)

    (
        app.version_store.active_core_path("assistant")
        / "agent/mcp/docs.yaml"
    ).unlink()
    core_without_mcp = app.core_loader.load(
        app.version_store.active_core_path("assistant")
    )
    await _prepare_turn(app, core_without_mcp, turn)

    assert not core_without_mcp.mcp_servers
    assert connection.closed is True
    await app.close()


@pytest.mark.asyncio
async def test_mcp_session_eviction_closes_only_that_sessions_connections(
    tmp_path,
):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    _write_mcp_server(
        app,
        "docs",
        "transport: stdio\n"
        "command: fake-docs\n"
        "approval_policy: auto\n",
    )
    core = app.core_loader.load(
        app.version_store.active_core_path("assistant")
    )
    turn_a = TurnContext(
        session_id="session_A",
        turn_id="turn_A",
        core_id=core.core_id,
        core_revision=core.revision,
        user_input=AgentInput(content="A"),
    )
    turn_b = TurnContext(
        session_id="session_B",
        turn_id="turn_B",
        core_id=core.core_id,
        core_revision=core.revision,
        user_input=AgentInput(content="B"),
    )
    connection_a = FakeMcpConnection([FakeListedTool(name="lookup")])
    connection_b = FakeMcpConnection([FakeListedTool(name="lookup")])
    connections = iter([connection_a, connection_b])
    app.tool_runtime.mcp_runtime.client_factory = lambda *_args: next(
        connections
    )

    await _prepare_turn(app, core, turn_a)
    await _prepare_turn(app, core, turn_b)
    await app.tool_runtime.mcp_runtime.evict_session(turn_a.session_id)

    assert connection_a.closed is True
    assert connection_b.closed is False
    assert app.tool_runtime.resolve_effects(
        core,
        turn=turn_a,
    ).entry_for("docs__lookup") is None
    assert app.tool_runtime.resolve_effects(
        core,
        turn=turn_b,
    ).entry_for("docs__lookup") is not None
    await app.close()
    assert connection_b.closed is True


@pytest.mark.asyncio
async def test_mcp_session_eviction_closes_all_server_connections(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    for server_id in ("alpha", "beta"):
        _write_mcp_server(
            app,
            server_id,
            "transport: stdio\n"
            f"command: fake-{server_id}\n"
            "approval_policy: auto\n",
        )
    alpha = FakeMcpConnection([FakeListedTool(name="lookup")])
    beta = FakeMcpConnection([FakeListedTool(name="lookup")])
    app.tool_runtime.mcp_runtime.client_factory = (
        lambda server, *_args: alpha if server.server_id == "alpha" else beta
    )
    core = app.core_loader.load(
        app.version_store.active_core_path("assistant")
    )
    turn = _turn(core)
    await _prepare_turn(app, core, turn)

    await app.tool_runtime.mcp_runtime.evict_session(turn.session_id)

    assert alpha.closed is True
    assert beta.closed is True
    assert not app.tool_runtime.mcp_runtime.entries_for(core, turn=turn)
    await app.close()


@pytest.mark.asyncio
async def test_mcp_session_eviction_wins_race_with_first_prepare(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    _write_mcp_server(
        app,
        "docs",
        "transport: stdio\n"
        "command: fake-docs\n"
        "approval_policy: auto\n",
    )
    core = app.core_loader.load(
        app.version_store.active_core_path("assistant")
    )
    turn = _turn(core)
    connection = FakeMcpConnection([FakeListedTool(name="lookup")])
    runtime = app.tool_runtime.mcp_runtime
    factory_calls = 0

    def factory(*_args):
        nonlocal factory_calls
        factory_calls += 1
        return connection

    runtime.client_factory = factory

    # Queue eviction before the first prepare reaches the runtime lock. This
    # deterministically covers the former window where eviction snapshotted no
    # catalog lock and prepare later published an orphaned connection.
    await runtime._lock.acquire()
    eviction = asyncio.create_task(runtime.evict_session(turn.session_id))
    await asyncio.sleep(0)
    prepare = asyncio.create_task(_prepare_turn(app, core, turn))
    await asyncio.sleep(0)
    runtime._lock.release()

    await eviction
    with pytest.raises(McpRuntimeError, match="session was evicted"):
        await prepare

    assert factory_calls == 0
    assert connection.list_calls == 0
    assert app.tool_runtime.resolve_effects(
        core,
        turn=turn,
    ).entry_for("docs__lookup") is None
    await app.close()


@pytest.mark.asyncio
async def test_start_new_session_drains_previous_mcp_connection(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    _write_mcp_server(
        app,
        "docs",
        "transport: stdio\n"
        "command: fake-docs\n"
        "approval_policy: auto\n",
    )
    core = app.core_loader.load(
        app.version_store.active_core_path("assistant")
    )
    previous_session_id = app.runner.session_id
    previous_turn = TurnContext(
        session_id=previous_session_id,
        turn_id="turn_previous",
        core_id=core.core_id,
        core_revision=core.revision,
        user_input=AgentInput(content="previous"),
    )
    connection = FakeMcpConnection([FakeListedTool(name="lookup")])
    app.tool_runtime.mcp_runtime.client_factory = lambda *_args: connection
    await _prepare_turn(app, core, previous_turn)

    new_session_id = app.runner.start_new_session()
    await app.runner.background_tasks.drain(include_runtime_tasks=False)

    assert new_session_id != previous_session_id
    assert connection.closed is True
    assert app.tool_runtime.resolve_effects(
        core,
        turn=previous_turn,
    ).entry_for("docs__lookup") is None
    await app.close()


def test_start_new_session_without_running_loop_closes_previous_mcp_connection(
    tmp_path,
):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    _write_mcp_server(
        app,
        "docs",
        "transport: stdio\n"
        "command: fake-docs\n"
        "approval_policy: auto\n",
    )
    core = app.core_loader.load(
        app.version_store.active_core_path("assistant")
    )
    previous_turn = TurnContext(
        session_id=app.runner.session_id,
        turn_id="turn_previous",
        core_id=core.core_id,
        core_revision=core.revision,
        user_input=AgentInput(content="previous"),
    )
    connection = FakeMcpConnection([FakeListedTool(name="lookup")])
    app.tool_runtime.mcp_runtime.client_factory = lambda *_args: connection
    asyncio.run(_prepare_turn(app, core, previous_turn))

    app.runner.start_new_session()

    assert connection.closed is True
    asyncio.run(app.close())


@pytest.mark.asyncio
async def test_resume_session_drains_previous_mcp_connection(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    _write_mcp_server(
        app,
        "docs",
        "transport: stdio\n"
        "command: fake-docs\n"
        "approval_policy: auto\n",
    )
    core = app.core_loader.load(
        app.version_store.active_core_path("assistant")
    )
    resume_target = app.runner.session_id
    active_session_id = app.runner.start_new_session()
    await app.runner.background_tasks.drain(include_runtime_tasks=False)
    active_turn = TurnContext(
        session_id=active_session_id,
        turn_id="turn_active",
        core_id=core.core_id,
        core_revision=core.revision,
        user_input=AgentInput(content="active"),
    )
    connection = FakeMcpConnection([FakeListedTool(name="lookup")])
    app.tool_runtime.mcp_runtime.client_factory = lambda *_args: connection
    await _prepare_turn(app, core, active_turn)

    app.runner.resume_session(resume_target)
    await app.runner.background_tasks.drain(include_runtime_tasks=False)

    assert app.runner.session_id == resume_target
    assert connection.closed is True
    assert app.tool_runtime.resolve_effects(
        core,
        turn=active_turn,
    ).entry_for("docs__lookup") is None
    await app.close()


@pytest.mark.asyncio
async def test_mcp_authority_change_evicts_cached_session_catalog(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    _write_mcp_server(
        app,
        "docs",
        "transport: stdio\n"
        "command: fake-docs\n"
        "approval_policy: auto\n",
    )
    connection = FakeMcpConnection([FakeListedTool(name="lookup")])
    app.tool_runtime.mcp_runtime.client_factory = lambda *_args: connection
    core_allowed = app.core_loader.load(
        app.version_store.active_core_path("assistant")
    )
    turn_allowed = _turn(core_allowed)

    await _prepare_turn(app, core_allowed, turn_allowed)

    manifest_path = app.version_store.active_core_path("assistant") / "agent.yaml"
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    raw["capabilities"]["defaults"].pop("mcp.connect:docs")
    manifest_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    core_denied = app.core_loader.load(
        app.version_store.active_core_path("assistant")
    )
    turn_denied = _turn(core_denied)
    await _prepare_turn(app, core_denied, turn_denied)

    assert connection.closed is True
    assert app.tool_runtime.resolve_effects(
        core_denied,
        turn=turn_denied,
    ).entry_for("docs__lookup") is None
    await app.close()


@pytest.mark.asyncio
async def test_mcp_declaration_change_requires_connect_reapproval_before_restart(
    tmp_path,
):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    _write_mcp_server(
        app,
        "docs",
        "transport: stdio\n"
        "command: fake-docs-v1\n"
        "approval_policy: prompt\n",
    )
    provider = SequenceApprovalProvider(
        ["always_allow_for_session", "deny"]
    )
    app.approval_runtime.provider = provider
    first = FakeMcpConnection([FakeListedTool(name="lookup")])
    factory_calls = 0

    def factory(*_args):
        nonlocal factory_calls
        factory_calls += 1
        return first

    app.tool_runtime.mcp_runtime.client_factory = factory
    core_v1 = app.core_loader.load(
        app.version_store.active_core_path("assistant")
    )
    turn_v1 = _turn(core_v1)
    await _prepare_turn(app, core_v1, turn_v1)

    _write_mcp_server(
        app,
        "docs",
        "transport: stdio\n"
        "command: fake-docs-v2\n"
        "approval_policy: prompt\n",
    )
    app.approval_runtime.provider = provider
    core_v2 = app.core_loader.load(
        app.version_store.active_core_path("assistant")
    )
    turn_v2 = _turn(core_v2)
    await _prepare_turn(app, core_v2, turn_v2)

    assert first.closed is True
    assert factory_calls == 1
    assert app.tool_runtime.resolve_effects(
        core_v2,
        turn=turn_v2,
    ).entry_for("docs__lookup") is None
    decisions = [
        event["decision"]
        for event in app.runner.event_log.tail(50)
        if event["type"] == "approval.decided"
        and event["action"] == "mcp.connect"
    ]
    assert decisions == ["always_allow_for_session", "deny"]
    await app.close()
