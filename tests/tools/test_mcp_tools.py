from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
import yaml

from demiurge.app import create_app
from demiurge.core import McpServerDefinition
from demiurge.providers import ToolCall
from demiurge.sdk import AgentInput, TurnContext
from demiurge.security.approval import StaticApprovalProvider
from demiurge.security.capabilities import CapabilityFacade


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
        core_version=core.version,
        user_input=AgentInput(content="test"),
        state={},
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


async def _prepare(app):
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    await app.tool_runtime.prepare_for_turn(core, _turn(core), emit_event=app.runner.event_log.emit)
    return core


def _execute(app, core, name, arguments):
    return app.tool_runtime.execute(
        ToolCall(name=name, arguments=arguments, id=f"call_{name}"),
        core=core,
        turn=_turn(core),
        capability=CapabilityFacade(core),
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
