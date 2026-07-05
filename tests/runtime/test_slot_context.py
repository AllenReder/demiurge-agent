from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from demiurge.core import AgentInfo, CoreManifest, LoadedCore, SlotDefinition
from demiurge.runtime.control import RuntimeControlPlane
from demiurge.runtime.interactions import InteractionItem
from demiurge.runtime.session import SessionRuntime
from demiurge.runtime.slot_context import ModuleStateStores, SlotContextRuntime
from demiurge.runtime.slots import InputSlotRunRequest, ModuleInputBuilder, OutputSlotRunRequest
from demiurge.runtime.store import RuntimeStore
from demiurge.sdk import (
    AgentInput,
    AgentRunResult,
    AgentSpawnHandle,
    InputEnvelope,
    OutputEnvelope,
    RawInput,
    ToolResult,
    TurnContext,
)
from demiurge.security.capabilities import CapabilityFacade
from demiurge.storage import StateStore


def _slot(tmp_path: Path, slot_id: str, *, kind: str = "input", history_policy: str = "persist") -> SlotDefinition:
    root = tmp_path / kind / slot_id
    root.mkdir(parents=True)
    return SlotDefinition(
        kind=kind,
        slot_id=slot_id,
        path=root,
        relative_path=f"agent/{kind}/{slot_id}",
        manifest={},
        history_policy=history_policy,
    )


def _core(tmp_path: Path) -> LoadedCore:
    capabilities = {
        "defaults": {
            "agents.run:*": True,
            "agents.spawn:*": True,
            "skill.activate": True,
            "state.core.read": True,
            "state.core.read:*": True,
            "state.core.write:*": True,
            "state.session.read": True,
            "state.session.read:*": True,
            "state.session.write:*": True,
            "tool.call:*": True,
        }
    }
    manifest = CoreManifest(agent=AgentInfo(id="assistant"), capabilities=capabilities)
    return LoadedCore(
        root=tmp_path / "core",
        manifest_path=tmp_path / "core" / "agent.yaml",
        manifest=manifest,
        raw_manifest=manifest.model_dump(),
        soul="",
        bootstrap_slots=[],
        bootstrap_pipeline=None,
        bootstrap_enabled=False,
        input_slots=[],
        output_slots=[],
        input_pipeline=None,
        output_pipeline=None,
        tool_slots=[],
        skills=[],
        schedules=[],
        mcp_servers=[],
    )


def _turn() -> TurnContext:
    return TurnContext(
        session_id="session_1",
        turn_id="turn_1",
        core_id="assistant",
        core_revision="rev_1",
        user_input=AgentInput(content="hello"),
    )


def _sessions(tmp_path: Path) -> SessionRuntime:
    runtime = SessionRuntime(control_plane=RuntimeControlPlane(RuntimeStore(tmp_path / "runtime.sqlite3")))
    runtime.ensure_session("session_1", core_id="assistant", core_revision="rev_1", channel="tui")
    runtime.start_turn(session_id="session_1", turn_id="turn_1", input_ref="inbound:1")
    runtime.append_message("session_1", role="user", content="history user", turn_id="turn_1")
    runtime.append_message(
        "session_1",
        role="assistant",
        content="history assistant",
        turn_id="turn_1",
        metadata={"step_id": "step_1"},
    )
    return runtime


def _state_stores(tmp_path: Path) -> ModuleStateStores:
    return ModuleStateStores(
        core=StateStore.core(tmp_path / "home", "assistant"),
        session=StateStore.session(tmp_path / "home", core_id="assistant", session_id="session_1"),
    )


class _Host:
    def __init__(self, tmp_path: Path):
        self.home = tmp_path / "home"
        self.session_id = "session_1"
        self.workspace = str(tmp_path / "workspace")
        Path(self.workspace).mkdir()
        self.sessions = _sessions(tmp_path)
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.committed: list[Any] = []
        self.scheduled: list[InteractionItem] = []

    def emit_event(self, event_type: str, **payload: Any) -> dict[str, Any]:
        self.events.append((event_type, payload))
        return {"type": event_type, **payload}

    def commit_module_delivery_request(self, request, *, turn, slot, interaction_metadata):
        self.committed.append((request, turn, slot, interaction_metadata))
        return InteractionItem(kind=f"delivery:{request.kind}", metadata=dict(request.metadata))

    def schedule_interaction_item(self, item: InteractionItem, *, turn, interaction_metadata) -> None:
        self.scheduled.append(item)

    async def execute_tool(self, *args, **kwargs) -> ToolResult:
        return ToolResult(content="tool result")

    async def run_child_agent(self, **kwargs) -> AgentRunResult:
        return AgentRunResult(content="child", core_id="assistant", session_id="child_session", turn_id="child_turn")

    def spawn_child_agent(self, **kwargs) -> AgentSpawnHandle:
        return AgentSpawnHandle(task_id="task_1", core_id="assistant", session_id="child_session")


def _capability(core: LoadedCore) -> CapabilityFacade:
    return CapabilityFacade(core)


def test_build_input_context_exposes_authored_slot_context_and_state(tmp_path):
    host = _Host(tmp_path)
    runtime = SlotContextRuntime(host)
    core = _core(tmp_path)
    slot = _slot(tmp_path, "prefix")
    builder = ModuleInputBuilder()
    request = InputSlotRunRequest(
        slot=slot,
        core=core,
        turn=_turn(),
        capability=_capability(core),
        envelope=InputEnvelope(raw_text="hello", metadata={"channel": "tui"}, attachments=[{"kind": "file"}]),
        raw_input=RawInput(text="hello", metadata={"channel": "tui"}, attachments=({"kind": "file"},)),
        builder=builder,
        builder_writable=True,
        state_stores=_state_stores(tmp_path),
        interaction_metadata={"channel": "tui"},
        activated=set(),
        contributions=[],
    )

    build = runtime.build_input_context(request, items=[])
    ctx = build.context

    assert ctx.slot_id == "prefix"
    assert ctx.slot_path == "agent/input/prefix"
    assert ctx.input.raw_text == "hello"
    assert ctx.input.attachments == ({"kind": "file"},)
    assert not hasattr(ctx, "result")

    ctx.input.add("user", "from input slot")
    ctx.input.add("system", "system context")
    assert builder.section_text("user") == "from input slot"
    assert builder.section_text("system") == "system context"

    recent = ctx.history.recent_messages(2)
    assert [message.role for message in recent] == ["user", "assistant"]
    assert recent[-1].step_id == "step_1"

    assert ctx.state.core.set("profile.mood", "focused") == "focused"
    assert ctx.state.session.merge("counter", {"count": 1}) == {"count": 1}
    assert ctx.state.core.get("profile.mood") == "focused"
    assert ctx.state.session.snapshot()["counter"]["count"] == 1
    assert [event[0] for event in host.events] == ["state.module_updated", "state.module_updated"]


def test_build_output_context_exposes_output_and_result_clients(tmp_path):
    host = _Host(tmp_path)
    runtime = SlotContextRuntime(host)
    core = _core(tmp_path)
    slot = _slot(tmp_path, "summary", kind="output")
    result_client = runtime.result_client(writable=True)
    request = OutputSlotRunRequest(
        slot=slot,
        core=core,
        turn=_turn(),
        capability=_capability(core),
        envelope=OutputEnvelope(content="assistant text", metadata={"format": "plain"}),
        current_output="assistant text",
        tool_records=[],
        state_stores=_state_stores(tmp_path),
        interaction_metadata={"channel": "tui"},
        result_client=result_client,
    )

    build = runtime.build_output_context(request, items=[])
    ctx = build.context

    assert ctx.slot_id == "summary"
    assert ctx.slot_path == "agent/output/summary"
    assert ctx.output.content == "assistant text"
    assert ctx.output.response_text == "assistant text"
    assert ctx.output.metadata == {"format": "plain"}
    assert ctx.result is result_client
    assert not hasattr(ctx, "input")

    assert ctx.result.set({"summary": "done"}) == {"summary": "done"}
    assert ctx.result.value == {"summary": "done"}


def test_parallel_output_context_can_emit_transient_updates_but_cannot_write_history_or_result(tmp_path):
    host = _Host(tmp_path)
    runtime = SlotContextRuntime(host)
    core = _core(tmp_path)
    slot = _slot(tmp_path, "background", kind="output")
    result_client = runtime.result_client(writable=False)
    items: list[InteractionItem] = []
    request = OutputSlotRunRequest(
        slot=slot,
        core=core,
        turn=_turn(),
        capability=_capability(core),
        envelope=OutputEnvelope(content="assistant text"),
        current_output="assistant text",
        tool_records=[],
        state_stores=_state_stores(tmp_path),
        interaction_metadata={"channel": "tui"},
        result_client=result_client,
        background=True,
    )

    build = runtime.build_output_context(request, items=items)
    ctx = build.context

    assert build.io_client.allow_write_history is False
    ctx.output.send_text("background update")
    assert [item.kind for item in items] == ["delivery:message"]
    assert items[0].metadata["background"] is True
    assert host.scheduled == items

    with pytest.raises(RuntimeError, match="parallel output modules cannot write session history"):
        ctx.output.send_text("persist me", write_history=True)
    with pytest.raises(RuntimeError, match="parallel output modules cannot modify the current agent result"):
        ctx.result.set({"value": 1})
