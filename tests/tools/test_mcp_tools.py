from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
import yaml

from demiurge.app import create_app
from demiurge.core import McpServerDefinition
from demiurge.providers import ToolCall
from demiurge.runtime.scope import PrincipalScopeResolver
from demiurge.sdk import AgentInput, TurnContext
from demiurge.security.approval import StaticApprovalProvider
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
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.closed = False

    async def list_tools(self):
        return self.tools

    async def call_tool(self, name, arguments, *, timeout_seconds):
        self.calls.append((name, dict(arguments)))
        return self.result

    async def close(self):
        self.closed = True


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
    await app.tool_runtime.prepare_for_turn(core, _turn(core), emit_event=app.runner.event_log.emit)
    return core


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


@pytest.mark.asyncio
async def test_mcp_discovery_adds_sanitized_filtered_tool(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    _write_mcp_server(
        app,
        "docs server",
        "transport: stdio\n"
        "command: fake-mcp\n"
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
    await app.tool_runtime.prepare_for_turn(core, turn_a)
    await app.tool_runtime.prepare_for_turn(core, turn_b)

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
    await app.tool_runtime.prepare_for_turn(core, turn)

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
    app.approval_runtime.provider = StaticApprovalProvider("deny")
    connection = FakeMcpConnection([FakeListedTool(name="lookup")])
    app.tool_runtime.mcp_runtime.client_factory = lambda *_args: connection
    core = await _prepare(app)

    result = await _execute(app, core, "docs__lookup", {"q": "x"})

    assert result.is_error is True
    assert "approval denied" in result.content
    assert connection.calls == []


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

    await app.tool_runtime.prepare_for_turn(core, turn_a, emit_event=app.runner.event_log.emit)
    await app.tool_runtime.prepare_for_turn(core, turn_b, emit_event=app.runner.event_log.emit)
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
        "command: fake-mcp\n",
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
