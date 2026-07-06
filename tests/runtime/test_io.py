from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from demiurge.providers import ToolCall
from demiurge.runtime.control import RuntimeControlPlane
from demiurge.runtime.interactions import InteractionItem
from demiurge.runtime.io import TurnIO
from demiurge.runtime.session import SessionRuntime
from demiurge.runtime.store import RuntimeStore
from demiurge.sdk import AgentInput, ToolResult, TurnContext
from demiurge.tools.records import ToolExecutionRecord


def _session_runtime(tmp_path: Path) -> SessionRuntime:
    runtime = SessionRuntime(control_plane=RuntimeControlPlane(RuntimeStore(tmp_path / "runtime.sqlite3")))
    runtime.ensure_session("session_1", core_id="assistant", core_revision="rev_1", channel="tui")
    runtime.start_turn(session_id="session_1", turn_id="turn_1", input_ref="inbound:1")
    return runtime


def _turn() -> TurnContext:
    return TurnContext(
        session_id="session_1",
        turn_id="turn_1",
        core_id="assistant",
        core_revision="rev_1",
        user_input=AgentInput(content="hello"),
    )


class _Host:
    def __init__(self, tmp_path: Path):
        self.session_id = "session_1"
        self.session_runtime = _session_runtime(tmp_path)
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.scheduled: list[InteractionItem] = []
        self.dispatched: list[InteractionItem] = []
    def emit_event(self, event_type: str, **payload) -> dict:
        self.events.append((event_type, dict(payload)))
        return {"type": event_type, **payload}

    def truncate_model_content(self, content: str) -> str:
        return content[:20]

    def tool_result_model_content(self, result: ToolResult) -> str:
        return f"model:{result.model_output or result.content}"

    def schedule_interaction_item(self, item: InteractionItem, *, turn: TurnContext, interaction_metadata: dict) -> None:
        self.scheduled.append(item)

    async def dispatch_interaction_item_now(
        self,
        item: InteractionItem,
        *,
        turn: TurnContext,
        interaction_metadata: dict,
    ) -> None:
        item.set_dispatch_status("delivered")
        self.dispatched.append(item)


def test_turn_io_send_user_persists_message_and_emits_event(tmp_path):
    host = _Host(tmp_path)
    runtime = TurnIO(host)

    assert runtime.send_user(turn_id="turn_1", content="", interaction_metadata={"channel": "tui"}) is None
    message = runtime.send_user(turn_id="turn_1", content="hello", interaction_metadata={"channel": "tui"})

    assert message is not None
    assert message.role == "user"
    assert message.content == "hello"
    assert [stored.content for stored in host.session_runtime.read_messages("session_1")] == ["hello"]
    assert host.events == [
        (
            "message.persisted",
            {
                "turn_id": "turn_1",
                "message_id": message.id,
                "role": "user",
                "kind": "message",
                "channel": "tui",
            },
        )
    ]


@pytest.mark.asyncio
async def test_turn_io_send_assistant_step_persists_and_schedules_visible_interim(tmp_path):
    host = _Host(tmp_path)
    runtime = TurnIO(host)
    call = ToolCall(id="call_1", name="tools_list", arguments={})

    message, items = await runtime.send_assistant_step(
        turn=_turn(),
        step_id="step_1",
        content="checking",
        tool_calls=[call],
        interaction_metadata={"channel": "tui"},
    )

    assert message.role == "assistant"
    assert message.visible is True
    assert message.metadata["tool_calls"] == [{"name": "tools_list", "arguments": {}, "id": "call_1"}]
    assert [item.kind for item in items] == ["delivery"]
    assert host.scheduled == items
    assert items[0].delivery is not None
    assert items[0].delivery.text == "checking"
    assert [event[0] for event in host.events] == ["message.persisted", "message.interim"]


@pytest.mark.asyncio
async def test_turn_io_send_assistant_step_with_empty_content_only_persists_message(tmp_path):
    host = _Host(tmp_path)
    runtime = TurnIO(host)

    message, items = await runtime.send_assistant_step(
        turn=_turn(),
        step_id="step_1",
        content="",
        tool_calls=[],
        interaction_metadata={"channel": "tui"},
    )

    assert message.role == "assistant"
    assert message.visible is False
    assert items == []
    assert host.scheduled == []
    assert [event[0] for event in host.events] == ["message.persisted"]


def test_turn_io_send_tool_result_persists_hidden_tool_message_and_schedules_item(tmp_path):
    host = _Host(tmp_path)
    runtime = TurnIO(host)
    record = ToolExecutionRecord(
        call=ToolCall(id="call_1", name="read_file", arguments={"path": "note.txt"}),
        result=ToolResult(content="file content"),
    )

    item = runtime.send_tool_result(
        turn=_turn(),
        step_id="step_1",
        record=record,
        interaction_metadata={"channel": "tui"},
    )

    messages = host.session_runtime.read_messages("session_1")
    assert messages[-1].role == "tool"
    assert messages[-1].content == "model:file content"
    assert messages[-1].visible is False
    assert messages[-1].metadata["tool_call_id"] == "call_1"
    assert item.kind == "tool_result"
    assert item.metadata["message_id"] == messages[-1].id
    assert host.scheduled == [item]


@pytest.mark.asyncio
async def test_turn_io_dispatches_tool_call_lifecycle_items(tmp_path):
    host = _Host(tmp_path)
    runtime = TurnIO(host)
    call = ToolCall(id="call_1", name="tools_list", arguments={})
    record = ToolExecutionRecord(call=call, result=ToolResult(content="ok", is_error=False))

    started = await runtime.send_tool_call_started(
        turn=_turn(),
        step_id="step_1",
        call=call,
        interaction_metadata={"channel": "tui"},
    )
    finished = await runtime.send_tool_call_finished(
        turn=_turn(),
        step_id="step_1",
        record=record,
        interaction_metadata={"channel": "tui"},
    )

    assert [item.tool_call.status for item in host.dispatched if item.tool_call is not None] == ["running", "ok"]
    assert started.dispatch_status == "delivered"
    assert finished.dispatch_status == "delivered"
    assert host.session_runtime.read_messages("session_1")[-1].content == "model:ok"
