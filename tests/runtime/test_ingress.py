import asyncio
import contextlib

import pytest

from demiurge.runtime.control import RuntimeControlPlane
from demiurge.runtime.ingress import ConversationIngressState, ConversationTurnController, InboundQueueRuntime
from demiurge.runtime.interactions import InteractionInbound
from demiurge.runtime.scope import PrincipalScopeResolver
from demiurge.runtime.session import SessionRuntime
from demiurge.runtime.store import RuntimeStore
from demiurge.runtime.tasks import RuntimeTaskWorker


def _user(text: str = "user", *, source: str = "source") -> InteractionInbound:
    return InteractionInbound(channel="test", text=text, source=source, conversation_key="conversation")


def _completion(task_id: str, text: str | None = None) -> InteractionInbound:
    return InteractionInbound(
        channel="test",
        text=text or f"completion {task_id}",
        source="source",
        conversation_key="conversation",
        metadata={
            "trigger": "background_task",
            "task_id": task_id,
            "event_id": f"event_{task_id}",
            "completion_claim_id": f"claim_{task_id}",
        },
    )


async def _complete_task(worker: RuntimeTaskWorker, *, session_id: str = "session_1", summary: str = "done"):
    store = worker.control_plane.store
    resolver = PrincipalScopeResolver(store)
    sessions = SessionRuntime(control_plane=worker.control_plane)
    if not store.session_owner_exists(session_id):
        provisional = resolver.issue_conversation(
            channel="test",
            principal_key="principal_1",
            conversation_key="conversation_1",
            session_id=session_id,
        )
        sessions.create_session(
            session_id=session_id,
            core_id="assistant",
            core_revision="rev",
            principal_scope=provisional,
        )
    scope = resolver.conversation(
        channel="test",
        principal_key="principal_1",
        conversation_key="conversation_1",
        session_id=session_id,
    )
    worker.bind_turn_scope(session_id=session_id, turn_id="turn_1", scope=scope)

    async def task(ctx):
        ctx.append_log("line")
        return summary

    record = worker.start_task(
        kind="terminal.exec",
        owner_session_id=session_id,
        owner_turn_id="turn_1",
        source_tool="test_tool",
        task_factory=task,
    )
    await worker.wait(record.task_id, timeout_seconds=1)
    return next(event for event in worker.pending_events_for_session(session_id) if event.task_id == record.task_id)


class _Route:
    pass


class _Runtime:
    def __init__(self, runner):
        self.runner = runner


class _Runner:
    def __init__(self, *, session_id: str = "session_1", task_worker=None):
        self.session_id = session_id
        self.task_worker = task_worker


@pytest.mark.asyncio
async def test_next_inbound_prefers_user_and_merges_queued_completions():
    queue = InboundQueueRuntime()
    await queue.put(_completion("1"))
    await queue.put(_user("hello"))
    await queue.put(_completion("2"))
    await queue.put(_user("later"))

    selected = queue.next_inbound()

    assert selected.text.startswith("hello")
    assert "completion 1" in selected.text
    assert "completion 2" in selected.text
    assert selected.metadata["merged_background_tasks"] == ["1", "2"]
    assert selected.metadata["completion_claims"] == [
        {"event_id": "event_1", "claim_id": "claim_1"},
        {"event_id": "event_2", "claim_id": "claim_2"},
    ]
    assert queue.qsize() == 1
    assert queue.next_inbound().text == "later"


@pytest.mark.asyncio
async def test_next_inbound_returns_completion_when_no_user_input_exists():
    queue = InboundQueueRuntime()
    completion = _completion("1")
    await queue.put(completion)

    assert queue.next_inbound() is completion
    assert queue.empty()


@pytest.mark.asyncio
async def test_clear_can_preserve_background_completions():
    queue = InboundQueueRuntime()
    await queue.put(_user("first"))
    await queue.put(_completion("1"))
    await queue.put(_user("second"))

    removed = queue.clear(preserve_completions=True)

    assert removed == 2
    assert queue.qsize() == 1
    assert queue.next_inbound().metadata["task_id"] == "1"


def test_next_inbound_raises_queue_empty_when_empty():
    queue = InboundQueueRuntime()

    with pytest.raises(asyncio.QueueEmpty):
        queue.next_inbound()


@pytest.mark.asyncio
async def test_merge_completions_into_preserves_ordinary_queued_inputs():
    queue = InboundQueueRuntime()
    await queue.put(_completion("queued"))
    await queue.put(_user("later"))
    stored = [_completion("stored")]

    selected = queue.merge_completions_into(_user("now"), stored_completions=stored)

    assert selected.text.startswith("now")
    assert "completion stored" in selected.text
    assert "completion queued" in selected.text
    assert selected.metadata["merged_background_tasks"] == ["stored", "queued"]
    assert queue.qsize() == 1
    assert queue.next_inbound().text == "later"


def test_conversation_ingress_state_remembers_route_and_builds_completion_route():
    state = ConversationIngressState(runtime=_Runtime(_Runner()), busy_mode="interrupt", route_binding=_Route())
    metadata = {"source_kind": "test"}
    inbound = InteractionInbound(
        channel="test",
        text="hello",
        source="source_1",
        reply_to="reply_1",
        conversation_key="conversation_1",
        metadata=metadata,
    )

    state.remember_route(inbound)
    metadata["source_kind"] = "mutated"
    route = state.completion_route("test")

    assert state.source == "source_1"
    assert state.reply_to == "reply_1"
    assert state.conversation_key == "conversation_1"
    assert state.metadata == {"source_kind": "test"}
    assert route.channel == "test"
    assert route.source == "source_1"
    assert route.reply_to == "reply_1"
    assert route.conversation_key == "conversation_1"
    assert route.metadata == {"source_kind": "test"}


def test_conversation_ingress_state_uses_fallback_source_for_completion_route():
    state = ConversationIngressState(runtime=_Runtime(_Runner()), busy_mode="interrupt", route_binding=_Route())

    route = state.completion_route("test", fallback_source="fallback")

    assert route.source == "fallback"


def test_conversation_ingress_state_reads_runner_session_id():
    state = ConversationIngressState(
        runtime=_Runtime(_Runner(session_id="session_2")),
        busy_mode="interrupt",
        route_binding=_Route(),
    )

    assert state.session_id == "session_2"


@pytest.mark.asyncio
async def test_conversation_ingress_state_claims_pending_completions(tmp_path):
    worker = RuntimeTaskWorker(control_plane=RuntimeControlPlane(RuntimeStore(tmp_path / "runtime.sqlite3")))
    event = await _complete_task(worker)
    state = ConversationIngressState(
        runtime=_Runtime(_Runner(task_worker=worker)),
        busy_mode="interrupt",
        route_binding=_Route(),
        source="source_1",
        conversation_key="conversation_1",
    )

    inbounds = state.claim_pending_completions(channel="test", owner_id="bridge:test")

    assert [inbound.metadata["event_id"] for inbound in inbounds] == [event.event_id]
    assert inbounds[0].source == "source_1"
    assert inbounds[0].conversation_key == "conversation_1"
    assert worker.pending_events_for_session("session_1") == []


@pytest.mark.asyncio
async def test_conversation_ingress_state_claims_single_completion_event(tmp_path):
    worker = RuntimeTaskWorker(control_plane=RuntimeControlPlane(RuntimeStore(tmp_path / "runtime.sqlite3")))
    event = await _complete_task(worker)
    state = ConversationIngressState(
        runtime=_Runtime(_Runner()),
        busy_mode="interrupt",
        route_binding=_Route(),
        source="source_1",
        conversation_key="conversation_1",
    )

    inbound = state.claim_completion_event(
        event,
        channel="test",
        owner_id="bridge:test",
        task_worker=worker,
    )

    assert inbound is not None
    assert inbound.metadata["event_id"] == event.event_id
    assert inbound.source == "source_1"
    assert inbound.conversation_key == "conversation_1"
    assert worker.pending_events_for_session("session_1") == []


@pytest.mark.asyncio
async def test_conversation_turn_controller_queue_busy_mode_keeps_active_task():
    state = ConversationIngressState(runtime=_Runtime(_Runner()), busy_mode="queue", route_binding=_Route())
    active = asyncio.create_task(asyncio.sleep(60))
    state.active_task = active
    notifications = []

    async def notify(decision):
        notifications.append((decision.kind, active.done()))

    decision = await ConversationTurnController(state).handle_busy_inbound(_user("second"), notify=notify)

    assert decision.kind == "queue"
    assert decision.notify is True
    assert decision.cancel_active is False
    assert notifications == [("queue", False)]
    assert active.done() is False
    assert state.queue.next_inbound().text == "second"
    active.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await active


@pytest.mark.asyncio
async def test_conversation_turn_controller_interrupt_notifies_before_cancelling_active_task():
    state = ConversationIngressState(runtime=_Runtime(_Runner()), busy_mode="interrupt", route_binding=_Route())
    active = asyncio.create_task(asyncio.sleep(60))
    state.active_task = active
    notifications = []

    async def notify(decision):
        notifications.append((decision.kind, active.cancelled(), active.done()))

    decision = await ConversationTurnController(state).handle_busy_inbound(_user("second"), notify=notify)

    assert decision.kind == "interrupt"
    assert decision.notify is True
    assert decision.cancel_active is True
    assert notifications == [("interrupt", False, False)]
    assert state.queue.next_inbound().text == "second"
    with contextlib.suppress(asyncio.CancelledError):
        await active
    assert active.cancelled() is True


@pytest.mark.asyncio
async def test_conversation_turn_controller_finish_clears_only_owned_active_task():
    state = ConversationIngressState(runtime=_Runtime(_Runner()), busy_mode="interrupt", route_binding=_Route())
    active = asyncio.create_task(asyncio.sleep(60))
    unrelated = asyncio.create_task(asyncio.sleep(60))
    state.active_task = active
    controller = ConversationTurnController(state)

    try:
        assert controller.finish(unrelated) is False
        assert state.active_task is active
        assert controller.finish(active) is True
        assert state.active_task is None
    finally:
        active.cancel()
        unrelated.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await active
        with contextlib.suppress(asyncio.CancelledError):
            await unrelated


@pytest.mark.asyncio
async def test_conversation_turn_controller_queue_and_drain_starts_input_when_idle():
    state = ConversationIngressState(runtime=_Runtime(_Runner()), busy_mode="interrupt", route_binding=_Route())
    controller = ConversationTurnController(state)
    started = []

    async def run(inbound):
        try:
            started.append(inbound.text)
        finally:
            ConversationTurnController(state).finish(asyncio.current_task())

    assert await controller.queue_and_drain_if_idle(_user("queued"), run) is True
    task = state.active_task
    assert task is not None
    await task
    assert started == ["queued"]
    assert state.active_task is None


@pytest.mark.asyncio
async def test_conversation_turn_controller_enqueue_completion_queues_when_active_and_starts_when_idle():
    state = ConversationIngressState(runtime=_Runtime(_Runner()), busy_mode="interrupt", route_binding=_Route())
    controller = ConversationTurnController(state)
    active = asyncio.create_task(asyncio.sleep(60))
    state.active_task = active
    started = []

    async def run(inbound):
        try:
            started.append(inbound.text)
        finally:
            ConversationTurnController(state).finish(asyncio.current_task())

    try:
        assert await controller.enqueue_completion(_completion("active"), run) == "queued_running"
        assert state.queue.next_inbound().metadata["task_id"] == "active"
    finally:
        active.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await active
    state.active_task = None

    assert await controller.enqueue_completion(_completion("idle"), run) == "started"
    task = state.active_task
    assert task is not None
    await task
    assert started == ["completion idle"]
    assert state.active_task is None


@pytest.mark.asyncio
async def test_conversation_turn_controller_merges_pending_completions_from_store_and_queue(tmp_path):
    worker = RuntimeTaskWorker(control_plane=RuntimeControlPlane(RuntimeStore(tmp_path / "runtime.sqlite3")))
    event = await _complete_task(worker, summary="stored complete")
    state = ConversationIngressState(
        runtime=_Runtime(_Runner(task_worker=worker)),
        busy_mode="interrupt",
        route_binding=_Route(),
        source="source_1",
        conversation_key="conversation_1",
    )
    await state.queue.put(_completion("queued", "queued complete"))

    merged = ConversationTurnController(state).merge_pending_completions(
        _user("hello", source="source_1"),
        channel="test",
        owner_id="bridge:test:merge",
    )

    assert merged.text.startswith("hello")
    assert "stored complete" in merged.text
    assert "queued complete" in merged.text
    assert merged.metadata["merged_background_tasks"] == [event.task_id, "queued"]
    assert worker.pending_events_for_session("session_1") == []


@pytest.mark.asyncio
async def test_conversation_turn_controller_enqueue_completion_event_runs_before_enqueue_hook(tmp_path):
    worker = RuntimeTaskWorker(control_plane=RuntimeControlPlane(RuntimeStore(tmp_path / "runtime.sqlite3")))
    event = await _complete_task(worker)
    state = ConversationIngressState(
        runtime=_Runtime(_Runner(task_worker=worker)),
        busy_mode="interrupt",
        route_binding=_Route(),
        source="source_1",
        conversation_key="conversation_1",
    )
    observed: list[str] = []

    async def before_enqueue(inbound):
        observed.append(f"before:{inbound.metadata['task_id']}")
        assert state.active_task is None

    async def run(inbound):
        try:
            observed.append(f"run:{inbound.metadata['task_id']}")
        finally:
            ConversationTurnController(state).finish(asyncio.current_task())

    result = await ConversationTurnController(state).enqueue_completion_event(
        event,
        channel="test",
        owner_id="bridge:test:enqueue",
        run=run,
        before_enqueue=before_enqueue,
    )

    assert result.status == "started"
    assert result.inbound is not None
    task = state.active_task
    assert task is not None
    await task
    assert observed == [f"before:{event.task_id}", f"run:{event.task_id}"]
    assert worker.pending_events_for_session("session_1") == []


@pytest.mark.asyncio
async def test_conversation_turn_controller_enqueue_completion_event_requires_source_before_claiming(tmp_path):
    worker = RuntimeTaskWorker(control_plane=RuntimeControlPlane(RuntimeStore(tmp_path / "runtime.sqlite3")))
    event = await _complete_task(worker)
    state = ConversationIngressState(
        runtime=_Runtime(_Runner(task_worker=worker)),
        busy_mode="interrupt",
        route_binding=_Route(),
    )

    async def run(_inbound):
        raise AssertionError("completion should not run")

    result = await ConversationTurnController(state).enqueue_completion_event(
        event,
        channel="test",
        owner_id="bridge:test:enqueue",
        run=run,
        require_source=True,
    )

    assert result.status == "ignored_no_route"
    assert result.inbound is None
    assert [pending.event_id for pending in worker.pending_events_for_session("session_1")] == [event.event_id]


@pytest.mark.asyncio
async def test_conversation_turn_controller_enqueue_completion_event_reports_missing_claim(tmp_path):
    worker = RuntimeTaskWorker(control_plane=RuntimeControlPlane(RuntimeStore(tmp_path / "runtime.sqlite3")))
    event = await _complete_task(worker)
    worker.claim_pending_event(event.event_id, owner_id="other")
    state = ConversationIngressState(
        runtime=_Runtime(_Runner(task_worker=worker)),
        busy_mode="interrupt",
        route_binding=_Route(),
        source="source_1",
    )

    async def run(_inbound):
        raise AssertionError("completion should not run")

    result = await ConversationTurnController(state).enqueue_completion_event(
        event,
        channel="test",
        owner_id="bridge:test:enqueue",
        run=run,
    )

    assert result.status == "not_claimed"
    assert result.inbound is None
    assert state.active_task is None
