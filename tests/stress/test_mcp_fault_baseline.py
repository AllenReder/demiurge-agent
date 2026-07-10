from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest

from baseline_support import BaselineContractFailure
from demiurge.app import create_app
from demiurge.sdk import AgentInput, TurnContext


pytestmark = pytest.mark.stress


@dataclass(slots=True)
class ListedTool:
    name: str
    description: str = ""
    inputSchema: dict[str, Any] = field(
        default_factory=lambda: {"type": "object", "properties": {}}
    )


class BlockingDiscoveryConnection:
    def __init__(self, label: str, *, blocked: bool = True) -> None:
        self.label = label
        self.blocked = blocked
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.closed = False

    async def list_tools(self):
        self.entered.set()
        if self.blocked:
            await self.release.wait()
        return [ListedTool(name=f"tool-{self.label}")]

    async def call_tool(self, name, arguments, *, timeout_seconds):
        raise AssertionError("discovery baseline must not call tools")

    async def close(self):
        self.closed = True
        self.release.set()


def _write_server(app, server_id: str, *, connect_timeout_seconds: float = 0.05) -> None:
    root = app.version_store.active_core_path("assistant") / "agent" / "mcp"
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{server_id}.yaml").write_text(
        "transport: stdio\n"
        f"command: fake-{server_id}\n"
        f"connect_timeout_seconds: {connect_timeout_seconds}\n"
        "approval_policy: auto\n",
        encoding="utf-8",
    )


def _turn(core) -> TurnContext:
    return TurnContext(
        session_id="session_mcp_stress",
        turn_id="turn_mcp_stress",
        core_id=core.core_id,
        core_revision=core.revision,
        user_input=AgentInput(content="MCP stress baseline"),
    )


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    raises=BaselineContractFailure,
    reason="MCP-03: enabled server discovery is currently serial",
)
async def test_mcp_03_enabled_server_discovery_overlaps(tmp_path, baseline_recorder):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    _write_server(app, "alpha")
    _write_server(app, "beta")
    connections = {
        "alpha": BlockingDiscoveryConnection("alpha"),
        "beta": BlockingDiscoveryConnection("beta"),
    }
    app.tool_runtime.mcp_runtime.client_factory = (
        lambda server, *_args: connections[server.server_id]
    )
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    prepare_task = asyncio.create_task(
        app.tool_runtime.mcp_runtime.prepare_for_turn(core, _turn(core))
    )
    overlap_observed = False
    catalog = None
    try:
        await asyncio.wait_for(connections["alpha"].entered.wait(), timeout=2)
        with baseline_recorder.measure(
            "mcp_multi_server_discovery",
            finding="MCP-03",
            scale={"servers": 2, "connect_timeout_seconds": 0.05},
        ) as sample:
            try:
                await asyncio.wait_for(connections["beta"].entered.wait(), timeout=2)
                overlap_observed = True
            except TimeoutError:
                overlap_observed = False
            connections["alpha"].release.set()
            connections["beta"].release.set()
            catalog = await asyncio.wait_for(prepare_task, timeout=2)
            sample.observations.update(
                {
                    "overlap_observed": overlap_observed,
                    "tools": sorted(tool.name for tool in catalog.tools),
                    "diagnostics": len(catalog.diagnostics),
                }
            )
            sample.require(
                overlap_observed and len(catalog.tools) == 2,
                "independent MCP server discovery must overlap and preserve both catalogs",
            )
    finally:
        connections["alpha"].release.set()
        connections["beta"].release.set()
        if not prepare_task.done():
            prepare_task.cancel()
        await asyncio.gather(prepare_task, return_exceptions=True)
        await app.close()

@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    raises=BaselineContractFailure,
    reason="MCP-03: list_tools has no per-server timeout and a hung server hides later healthy servers",
)
async def test_mcp_03_hung_discovery_times_out_without_hiding_fast_server(
    tmp_path,
    baseline_recorder,
):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    _write_server(app, "alpha", connect_timeout_seconds=0.05)
    _write_server(app, "beta", connect_timeout_seconds=0.05)
    connections = {
        "alpha": BlockingDiscoveryConnection("alpha", blocked=True),
        "beta": BlockingDiscoveryConnection("beta", blocked=False),
    }
    app.tool_runtime.mcp_runtime.client_factory = (
        lambda server, *_args: connections[server.server_id]
    )
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    prepare_task = asyncio.create_task(
        app.tool_runtime.mcp_runtime.prepare_for_turn(core, _turn(core))
    )
    catalog = None
    completed_within_guard = False
    try:
        await asyncio.wait_for(connections["alpha"].entered.wait(), timeout=2)
        with baseline_recorder.measure(
            "mcp_hung_discovery_partial_success",
            finding="MCP-03",
            scale={"servers": 2, "hung_servers": 1, "connect_timeout_seconds": 0.05},
        ) as sample:
            try:
                catalog = await asyncio.wait_for(asyncio.shield(prepare_task), timeout=2)
                completed_within_guard = True
            except TimeoutError:
                completed_within_guard = False
            sample.observations.update(
                {
                    "completed_within_guard": completed_within_guard,
                    "fast_server_entered": connections["beta"].entered.is_set(),
                    "tools": [] if catalog is None else sorted(tool.name for tool in catalog.tools),
                    "diagnostics": 0 if catalog is None else len(catalog.diagnostics),
                }
            )
            sample.require(
                completed_within_guard
                and catalog is not None
                and any(tool.server_id == "beta" for tool in catalog.tools)
                and any(diagnostic.server_id == "alpha" for diagnostic in catalog.diagnostics),
                "hung MCP discovery must time out independently without hiding healthy servers",
            )
    finally:
        connections["alpha"].release.set()
        if not prepare_task.done():
            prepare_task.cancel()
        await asyncio.gather(prepare_task, return_exceptions=True)
        await app.close()
