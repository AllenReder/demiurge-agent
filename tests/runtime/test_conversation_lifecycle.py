from __future__ import annotations

import asyncio
import contextlib
from types import SimpleNamespace

import pytest

from demiurge.runtime.conversation_lifecycle import ConversationLifecycleConfig, ConversationLifecycleRuntime
from demiurge.runtime.ingress import ConversationIngressState
from demiurge.runtime.interactions import InteractionInbound, SessionRouteBinding
from demiurge.runtime.tasks import RuntimeTaskCompletionEvent


class FakeTaskWorker:
    def __init__(self, events: list[RuntimeTaskCompletionEvent] | None = None):
        self.events = list(events or [])
        self.claimed: dict[str, str] = {}
        self.callbacks = []

    def subscribe(self, callback):
        self.callbacks.append(callback)

        def unsubscribe() -> None:
            self.callbacks.remove(callback)

        return unsubscribe

    def emit(self, event: RuntimeTaskCompletionEvent) -> None:
        for callback in list(self.callbacks):
            callback(event)

    def pending_events_for_session(self, session_id: str) -> list[RuntimeTaskCompletionEvent]:
        return [event for event in self.events if event.owner_session_id == session_id]

    def claim_pending_event(self, event_id: str, *, owner_id: str):
        if event_id in self.claimed:
            return None
        self.claimed[event_id] = owner_id
        return SimpleNamespace(claim_id=f"claim-{event_id}")


def _event(event_id: str = "event_1", *, session_id: str = "session_chat") -> RuntimeTaskCompletionEvent:
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


def _inbound(text: str = "hello", *, conversation_key: str = "chat") -> InteractionInbound:
    return InteractionInbound(channel="test", text=text, source="user", conversation_key=conversation_key)


def _state(key: str, worker: FakeTaskWorker | None = None) -> ConversationIngressState:
    runner = SimpleNamespace(session_id=f"session_{key}", task_worker=worker)
    runtime = SimpleNamespace(runner=runner)
    return ConversationIngressState(
        runtime=runtime,
        busy_mode="interrupt",
        route_binding=SessionRouteBinding(route=SimpleNamespace()),
        conversation_key=key,
        source="user",
    )


def _runtime(worker: FakeTaskWorker | None, seen: list[str]):
    async def run_turn(state: ConversationIngressState, inbound: InteractionInbound) -> None:
        seen.append(inbound.text)

    return ConversationLifecycleRuntime(
        config=ConversationLifecycleConfig(
            channel="test",
            merge_owner_id="merge-owner",
            enqueue_owner_id="enqueue-owner",
            fallback_source="fallback",
        ),
        state_factory=lambda key: _state(key, worker),
        run_turn=run_turn,
    )


async def _wait_idle(state: ConversationIngressState) -> None:
    await asyncio.sleep(0)
    while state.active_task is not None:
        task = state.active_task
        with contextlib.suppress(asyncio.CancelledError):
            await task
        await asyncio.sleep(0)
        if state.active_task is task:
            break


@pytest.mark.asyncio
async def test_lifecycle_runtime_starts_finishes_and_drains_next_inbound():
    seen: list[str] = []
    turn_finished: list[bool] = []

    async def run_turn(state: ConversationIngressState, inbound: InteractionInbound) -> None:
        seen.append(inbound.text)
        if inbound.text == "first":
            await state.queue.put(_inbound("second"))

    async def after_turn(state: ConversationIngressState, drained: bool) -> None:
        turn_finished.append(drained)

    runtime = ConversationLifecycleRuntime(
        config=ConversationLifecycleConfig(
            channel="test",
            merge_owner_id="merge-owner",
            enqueue_owner_id="enqueue-owner",
        ),
        state_factory=lambda key: _state(key),
        run_turn=run_turn,
        after_turn=after_turn,
    )
    state = runtime.state_for_key("chat")

    runtime.start_turn(state, _inbound("first"))
    await _wait_idle(state)

    assert seen == ["first", "second"]
    assert turn_finished == [True, False]
    assert state.active_task is None


@pytest.mark.asyncio
async def test_lifecycle_runtime_can_skip_drain_when_adapter_is_closing():
    seen: list[str] = []
    turn_finished: list[bool] = []

    async def run_turn(state: ConversationIngressState, inbound: InteractionInbound) -> None:
        seen.append(inbound.text)
        await state.queue.put(_inbound("second"))

    async def after_turn(state: ConversationIngressState, drained: bool) -> None:
        turn_finished.append(drained)

    runtime = ConversationLifecycleRuntime(
        config=ConversationLifecycleConfig(
            channel="test",
            merge_owner_id="merge-owner",
            enqueue_owner_id="enqueue-owner",
        ),
        state_factory=lambda key: _state(key),
        run_turn=run_turn,
        should_drain=lambda state: False,
        after_turn=after_turn,
    )
    state = runtime.state_for_key("chat")

    runtime.start_turn(state, _inbound("first"))
    await _wait_idle(state)

    assert seen == ["first"]
    assert turn_finished == [False]
    assert state.queue.qsize() == 1
    assert state.active_task is None


@pytest.mark.asyncio
async def test_lifecycle_runtime_handles_busy_input_with_adapter_notice():
    seen: list[str] = []
    notices: list[tuple[str, str]] = []
    unblock = asyncio.Event()

    async def run_turn(state: ConversationIngressState, inbound: InteractionInbound) -> None:
        seen.append(inbound.text)
        await unblock.wait()

    async def notify_busy(state, inbound, decision) -> None:
        notices.append((inbound.text, decision.kind))

    runtime = ConversationLifecycleRuntime(
        config=ConversationLifecycleConfig(
            channel="test",
            merge_owner_id="merge-owner",
            enqueue_owner_id="enqueue-owner",
        ),
        state_factory=lambda key: _state(key),
        run_turn=run_turn,
        notify_busy=notify_busy,
    )
    state = runtime.state_for_key("chat")

    runtime.start_turn(state, _inbound("first"))
    await asyncio.sleep(0)
    accepted = await runtime.accept_inbound(state, _inbound("second"))
    unblock.set()
    await _wait_idle(state)

    assert accepted is False
    assert notices == [("second", "interrupt")]
    assert seen == ["first", "second"]


def test_lifecycle_runtime_merges_pending_completions():
    event = _event()
    worker = FakeTaskWorker([event])
    seen: list[str] = []
    runtime = _runtime(worker, seen)
    state = runtime.state_for_key("chat")

    merged = runtime.merge_pending(state, _inbound("hello"))

    assert merged.text.startswith("hello")
    assert "Pending background task events" in merged.text
    assert worker.claimed == {"event_1": "merge-owner"}


@pytest.mark.asyncio
async def test_lifecycle_runtime_enqueues_task_completion_from_subscription():
    event = _event()
    worker = FakeTaskWorker([event])
    seen: list[str] = []
    before_enqueue: list[str] = []
    after_enqueue: list[tuple[str, str]] = []

    async def run_turn(state: ConversationIngressState, inbound: InteractionInbound) -> None:
        seen.append(inbound.metadata["task_id"])

    async def before(state, task_event, inbound) -> None:
        before_enqueue.append(task_event.task_id)

    async def after(state, task_event, result) -> None:
        after_enqueue.append((task_event.task_id, result.status))

    runtime = ConversationLifecycleRuntime(
        config=ConversationLifecycleConfig(
            channel="test",
            merge_owner_id="merge-owner",
            enqueue_owner_id="enqueue-owner",
        ),
        state_factory=lambda key: _state(key, worker),
        run_turn=run_turn,
        before_completion_enqueue=before,
        after_completion_enqueue=after,
    )
    state = runtime.state_for_key("chat")

    worker.emit(event)
    await _wait_idle(state)

    assert worker.claimed == {"event_1": "enqueue-owner"}
    assert before_enqueue == ["task_1"]
    assert after_enqueue == [("task_1", "started")]
    assert seen == ["task_1"]
