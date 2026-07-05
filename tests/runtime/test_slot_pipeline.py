from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from demiurge.core import SlotDefinition
from demiurge.runtime.interactions import InteractionItem
from demiurge.runtime.slots import InputPipelineRequest, OutputPipelineRequest, SlotPipelineRuntime
from demiurge.sdk import AgentInput, InputEnvelope, TurnContext


def _slot(slot_id: str, *, kind: str = "input") -> SlotDefinition:
    return SlotDefinition(
        kind=kind,
        slot_id=slot_id,
        path=Path("/tmp") / slot_id,
        relative_path=f"agent/{kind}/{slot_id}",
        manifest={},
    )


def _turn() -> TurnContext:
    return TurnContext(
        session_id="session_1",
        turn_id="turn_1",
        core_id="assistant",
        core_revision="rev_1",
        user_input=AgentInput(content="raw"),
    )


class _ResultClient:
    def __init__(self, *, writable: bool = True, forks: list["_ResultClient"] | None = None):
        self.writable = writable
        self.forks = forks if forks is not None else []

    def fork(self, *, writable: bool) -> "_ResultClient":
        fork = _ResultClient(writable=writable, forks=self.forks)
        self.forks.append(fork)
        return fork


class _Host:
    def __init__(self):
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.background_tasks: list[asyncio.Task[Any]] = []
        self.flushed: list[list[str]] = []
        self.input_calls: list[tuple[str, bool, bool]] = []
        self.output_calls: list[tuple[str, bool, bool]] = []

    def emit_event(self, event_type: str, **payload: Any) -> dict[str, Any]:
        self.events.append((event_type, payload))
        return {"type": event_type, **payload}

    def track_background_task(self, task: asyncio.Task[Any]) -> None:
        self.background_tasks.append(task)

    async def run_input_slot(self, request):
        self.input_calls.append((request.slot.slot_id, request.builder_writable, request.background))
        assert request.turn.turn_id == "turn_1"
        assert request.raw_input.text == "hello"
        assert request.interaction_metadata.get("channel") in {None, "test"}
        if request.builder_writable:
            request.builder.add("user", f"{request.slot.slot_id}:{request.raw_input.text}")
            request.builder.add("system", f"system:{request.slot.slot_id}")
        return [InteractionItem(kind=f"input:{request.slot.slot_id}")]

    async def run_output_slot(self, request):
        self.output_calls.append((request.slot.slot_id, request.result_client.writable, request.background))
        assert request.turn.turn_id == "turn_1"
        assert request.current_output == "assistant"
        assert request.envelope.content == "assistant"
        return [InteractionItem(kind=f"output:{request.slot.slot_id}")]

    async def flush_background_items(self, items, *, turn, interaction_metadata):
        self.flushed.append([item.kind for item in items])


@pytest.mark.asyncio
async def test_input_pipeline_runs_serial_slots_and_flushes_parallel_without_prompt_write():
    host = _Host()
    runtime = SlotPipelineRuntime(host)

    result = await runtime.run_input(
        InputPipelineRequest(
            core=object(),
            turn=_turn(),
            capability=object(),
            envelope=InputEnvelope(raw_text="hello", metadata={"channel": "test"}),
            state_stores=object(),
            interaction_metadata={"channel": "test"},
            injected_system_context=["injected"],
            serial_slots=[_slot("serial_one"), _slot("serial_two")],
            parallel_slots=[_slot("parallel_one")],
        )
    )

    assert result.user_text == "serial_one:hello\n\nserial_two:hello"
    assert result.persisted_user_text == result.user_text
    assert [item.kind for item in result.items] == ["input:serial_one", "input:serial_two"]
    assert [item.content for item in result.context] == [
        "injected",
        "system:serial_one\n\nsystem:serial_two",
    ]
    assert host.input_calls == [
        ("serial_one", True, False),
        ("serial_two", True, False),
        ("parallel_one", False, True),
    ]
    assert host.flushed == [["input:parallel_one"]]
    assert host.events == [
        ("module.async_scheduled", {"turn_id": "turn_1", "slot": "agent/input/parallel_one", "kind": "input"})
    ]


@pytest.mark.asyncio
async def test_input_pipeline_requires_user_message():
    host = _Host()
    runtime = SlotPipelineRuntime(host)

    with pytest.raises(RuntimeError, match="input pipeline did not produce a user message"):
        await runtime.run_input(
            InputPipelineRequest(
                core=object(),
                turn=_turn(),
                capability=object(),
                envelope=InputEnvelope(raw_text="hello"),
                state_stores=object(),
                interaction_metadata={},
                serial_slots=[],
                parallel_slots=[],
            )
        )


@pytest.mark.asyncio
async def test_output_pipeline_runs_serial_slots_and_flushes_parallel_with_forked_result_client():
    host = _Host()
    runtime = SlotPipelineRuntime(host)
    result_client = _ResultClient()

    items = await runtime.run_output(
        OutputPipelineRequest(
            core=object(),
            turn=_turn(),
            capability=object(),
            current_output="assistant",
            tool_records=[],
            state_stores=object(),
            interaction_metadata={"channel": "test"},
            result_client=result_client,
            serial_slots=[_slot("serial", kind="output")],
            parallel_slots=[_slot("parallel", kind="output")],
        )
    )

    assert [item.kind for item in items] == ["output:serial"]
    assert host.output_calls == [
        ("serial", True, False),
        ("parallel", False, True),
    ]
    assert host.flushed == [["output:parallel"]]
    assert len(result_client.forks) == 1
    assert result_client.forks[0].writable is False
    assert host.events == [
        ("module.async_scheduled", {"turn_id": "turn_1", "slot": "agent/output/parallel", "kind": "output"})
    ]
