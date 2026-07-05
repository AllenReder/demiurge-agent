from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from demiurge.runtime.completion_delivery import CompletionDeliveryRuntime
from demiurge.runtime.ingress import ConversationIngressState
from demiurge.runtime.interactions import InteractionInbound, SessionRouteBinding
from demiurge.runtime.tasks import RuntimeTaskCompletionEvent


class FakeTaskWorker:
    def __init__(self, events: list[RuntimeTaskCompletionEvent] | None = None):
        self.events = list(events or [])
        self.claimed: dict[str, str] = {}

    def pending_events_for_session(self, session_id: str) -> list[RuntimeTaskCompletionEvent]:
        return [event for event in self.events if event.owner_session_id == session_id]

    def claim_pending_event(self, event_id: str, *, owner_id: str):
        if event_id in self.claimed:
            return None
        self.claimed[event_id] = owner_id
        return SimpleNamespace(claim_id=f"claim-{event_id}")


def _event(event_id: str = "event_1", *, session_id: str = "session_1") -> RuntimeTaskCompletionEvent:
    return RuntimeTaskCompletionEvent(
        event_id=event_id,
        task_id="task_1",
        kind="terminal.exec",
        owner_session_id=session_id,
        owner_turn_id="turn_1",
        source_tool="terminal",
        status="completed",
        summary="done",
    )


def _state(worker: FakeTaskWorker, *, source: str = "local") -> ConversationIngressState:
    runner = SimpleNamespace(session_id="session_1", task_worker=worker)
    runtime = SimpleNamespace(runner=runner)
    return ConversationIngressState(
        runtime=runtime,
        busy_mode="interrupt",
        route_binding=SessionRouteBinding(route=SimpleNamespace()),
        conversation_key="conversation_1",
        source=source,
    )


def _delivery(worker: FakeTaskWorker, *, require_source: bool = False) -> CompletionDeliveryRuntime:
    return CompletionDeliveryRuntime(
        channel="tui",
        merge_owner_id="merge-owner",
        enqueue_owner_id="enqueue-owner",
        task_worker=worker,
        require_source=require_source,
        fallback_source="fallback",
    )


def test_completion_delivery_runtime_merges_stored_completions_into_user_inbound():
    event = _event()
    worker = FakeTaskWorker([event])
    state = _state(worker)
    delivery = _delivery(worker)
    inbound = InteractionInbound(channel="tui", text="hello", source="local", conversation_key="conversation_1")

    merged = delivery.merge_pending_into(state, inbound)

    assert merged.text.startswith("hello")
    assert "[SYSTEM: Pending background task events merged into this user turn]" in merged.text
    assert "task_1" in merged.text
    assert merged.metadata["merged_background_tasks"] == ["task_1"]
    assert merged.metadata["completion_claims"] == [{"event_id": "event_1", "claim_id": "claim-event_1"}]
    assert worker.claimed == {"event_1": "merge-owner"}


def test_completion_delivery_runtime_leaves_background_completion_inbound_unmerged():
    event = _event()
    worker = FakeTaskWorker([event])
    state = _state(worker)
    delivery = _delivery(worker)
    inbound = InteractionInbound(
        channel="tui",
        text="done",
        source="local",
        conversation_key="conversation_1",
        metadata={"trigger": "background_task"},
    )

    merged = delivery.merge_pending_into(state, inbound)

    assert merged is inbound
    assert worker.claimed == {}


@pytest.mark.asyncio
async def test_completion_delivery_runtime_enqueues_completion_event_when_idle():
    event = _event()
    worker = FakeTaskWorker([event])
    state = _state(worker)
    delivery = _delivery(worker)
    seen: list[InteractionInbound] = []

    async def run(inbound: InteractionInbound) -> None:
        seen.append(inbound)

    result = await delivery.enqueue_event(state, event, run=run)
    assert result.status == "started"
    assert result.inbound is not None
    assert worker.claimed == {"event_1": "enqueue-owner"}

    assert state.active_task is not None
    await state.active_task
    assert seen
    assert seen[0].metadata["completion_claim_id"] == "claim-event_1"


@pytest.mark.asyncio
async def test_completion_delivery_runtime_can_require_known_route_source():
    event = _event()
    worker = FakeTaskWorker([event])
    state = _state(worker, source="")
    delivery = _delivery(worker, require_source=True)

    async def run(_inbound: InteractionInbound) -> None:
        raise AssertionError("completion should not run without a route")

    result = await delivery.enqueue_event(state, event, run=run)

    assert result.status == "ignored_no_route"
    assert result.inbound is None
    assert worker.claimed == {}
