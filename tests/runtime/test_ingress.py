import asyncio

import pytest

from demiurge.runtime.control import RuntimeControlPlane
from demiurge.runtime.ingress import ConversationIngressState, InboundQueueRuntime
from demiurge.runtime.interactions import InteractionInbound
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
