from __future__ import annotations

from types import SimpleNamespace

from demiurge.runtime.conversation_states import ConversationStateStore
from demiurge.runtime.ingress import ConversationIngressState
from demiurge.runtime.interactions import SessionRouteBinding
from demiurge.runtime.tasks import RuntimeTaskCompletionEvent


class FakeTaskWorker:
    def __init__(self) -> None:
        self.callbacks = []

    def subscribe(self, callback):
        self.callbacks.append(callback)

        def unsubscribe() -> None:
            self.callbacks.remove(callback)

        return unsubscribe

    def emit(self, event: RuntimeTaskCompletionEvent) -> None:
        for callback in list(self.callbacks):
            callback(event)


def _event(session_id: str = "session_1") -> RuntimeTaskCompletionEvent:
    return RuntimeTaskCompletionEvent(
        event_id=f"event_{session_id}",
        task_id="task_1",
        kind="terminal.exec",
        owner_session_id=session_id,
        owner_turn_id="turn_1",
        source_tool="terminal",
        status="succeeded",
        summary="done",
    )


def _state(session_id: str, worker: FakeTaskWorker | None = None) -> ConversationIngressState:
    runner = SimpleNamespace(session_id=session_id, task_worker=worker)
    runtime = SimpleNamespace(runner=runner)
    return ConversationIngressState(
        runtime=runtime,
        busy_mode="interrupt",
        route_binding=SessionRouteBinding(route=SimpleNamespace()),
        conversation_key=session_id,
    )


def test_conversation_state_store_caches_state_and_finds_by_session():
    worker = FakeTaskWorker()
    events: list[RuntimeTaskCompletionEvent] = []
    store = ConversationStateStore(
        state_factory=lambda key: _state(f"session-{key}", worker),
        on_task_completion=events.append,
    )

    first = store.state_for_key("chat-1")
    second = store.state_for_key("chat-1")

    assert first is second
    assert store.states == {"chat-1": first}
    assert store.state_for_session("session-chat-1") is first
    assert store.state_for_session("missing") is None
    assert len(worker.callbacks) == 1


def test_conversation_state_store_subscribes_each_distinct_task_worker():
    worker_a = FakeTaskWorker()
    worker_b = FakeTaskWorker()
    workers = {"chat-a": worker_a, "chat-b": worker_b}
    events: list[RuntimeTaskCompletionEvent] = []
    store = ConversationStateStore(
        state_factory=lambda key: _state(f"session-{key}", workers[key]),
        on_task_completion=events.append,
    )

    store.state_for_key("chat-a")
    store.state_for_key("chat-b")

    assert len(worker_a.callbacks) == 1
    assert len(worker_b.callbacks) == 1
    worker_a.emit(_event("session-chat-a"))
    worker_b.emit(_event("session-chat-b"))
    assert [event.owner_session_id for event in events] == ["session-chat-a", "session-chat-b"]


def test_conversation_state_store_deduplicates_shared_task_worker_subscription():
    worker = FakeTaskWorker()
    events: list[RuntimeTaskCompletionEvent] = []
    store = ConversationStateStore(
        state_factory=lambda key: _state(f"session-{key}", worker),
        on_task_completion=events.append,
    )

    store.state_for_key("chat-a")
    store.state_for_key("chat-b")

    assert len(worker.callbacks) == 1
    worker.emit(_event("session-chat-a"))
    assert [event.owner_session_id for event in events] == ["session-chat-a"]


def test_conversation_state_store_close_unsubscribes_all_workers():
    worker_a = FakeTaskWorker()
    worker_b = FakeTaskWorker()
    workers = {"chat-a": worker_a, "chat-b": worker_b}
    store = ConversationStateStore(
        state_factory=lambda key: _state(f"session-{key}", workers[key]),
        on_task_completion=lambda _event: None,
    )
    store.state_for_key("chat-a")
    store.state_for_key("chat-b")

    store.close()

    assert worker_a.callbacks == []
    assert worker_b.callbacks == []


def test_conversation_state_store_ignores_states_without_task_worker():
    store = ConversationStateStore(
        state_factory=lambda key: _state(f"session-{key}", None),
        on_task_completion=lambda _event: None,
    )

    state = store.state_for_key("chat-a")

    assert store.state_for_session("session-chat-a") is state
