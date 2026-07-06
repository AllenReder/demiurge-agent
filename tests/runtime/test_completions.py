import pytest

from demiurge.runtime.completions import (
    CompletionInbox,
    CompletionRoute,
    is_background_completion,
    merge_completion_inbounds,
)
from demiurge.runtime.control import RuntimeControlPlane
from demiurge.runtime.interactions import InteractionInbound
from demiurge.runtime.store import RuntimeStore
from demiurge.runtime.tasks import RuntimeTaskWorker


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


def _worker(tmp_path) -> RuntimeTaskWorker:
    control = RuntimeControlPlane(RuntimeStore(tmp_path / "runtime.sqlite3"))
    return RuntimeTaskWorker(control_plane=control)


@pytest.mark.asyncio
async def test_completion_inbox_claim_event_builds_synthetic_inbound(tmp_path):
    worker = _worker(tmp_path)
    inbox = CompletionInbox(worker)
    event = await _complete_task(worker, summary="background complete")

    inbound = inbox.claim_event(
        event,
        owner_id="bridge:test",
        route=CompletionRoute(
            channel="test",
            source="source_1",
            reply_to="reply_1",
            conversation_key="conversation_1",
            metadata={"route": "kept"},
        ),
    )

    assert inbound is not None
    assert is_background_completion(inbound)
    assert inbound.channel == "test"
    assert inbound.source == "source_1"
    assert inbound.reply_to == "reply_1"
    assert inbound.conversation_key == "conversation_1"
    assert inbound.metadata["route"] == "kept"
    assert inbound.metadata["event_id"] == event.event_id
    assert inbound.metadata["task_id"] == event.task_id
    assert inbound.metadata["completion_claim_id"]
    assert "[SYSTEM: Background task event]" in inbound.text
    assert "background complete" in inbound.text


def test_merge_completion_inbounds_preserves_user_route_and_records_claims():
    user = InteractionInbound(
        channel="test",
        text="user message",
        source="source_1",
        reply_to="reply_1",
        conversation_key="conversation_1",
        metadata={"user": True},
    )
    completion = InteractionInbound(
        channel="test",
        text="completion message",
        source="source_1",
        reply_to="reply_1",
        conversation_key="conversation_1",
        metadata={
            "trigger": "background_task",
            "task_id": "task_1",
            "event_id": "event_1",
            "completion_claim_id": "claim_1",
        },
    )

    merged = merge_completion_inbounds(user, [completion])

    assert merged.channel == user.channel
    assert merged.source == user.source
    assert merged.reply_to == user.reply_to
    assert merged.conversation_key == user.conversation_key
    assert merged.metadata["user"] is True
    assert merged.metadata["merged_background_tasks"] == ["task_1"]
    assert merged.metadata["completion_claims"] == [{"event_id": "event_1", "claim_id": "claim_1"}]
    assert merged.text.startswith("user message")
    assert "[SYSTEM: Pending background task events merged into this user turn]" in merged.text
    assert "completion message" in merged.text


@pytest.mark.asyncio
async def test_claim_pending_for_session_claims_each_event_once(tmp_path):
    worker = _worker(tmp_path)
    inbox = CompletionInbox(worker)
    event = await _complete_task(worker)

    inbounds = inbox.claim_pending_for_session(
        "session_1",
        owner_id="bridge:test",
        route=CompletionRoute(channel="test", source="source_1"),
    )
    second_claim = inbox.claim_pending_for_session(
        "session_1",
        owner_id="bridge:test",
        route=CompletionRoute(channel="test", source="source_1"),
    )

    assert [item.metadata["event_id"] for item in inbounds] == [event.event_id]
    assert second_claim == []
    assert worker.pending_events_for_session("session_1") == []


@pytest.mark.asyncio
async def test_ack_from_metadata_accepts_direct_and_merged_claim_shapes(tmp_path):
    worker = _worker(tmp_path)
    inbox = CompletionInbox(worker)
    direct_event = await _complete_task(worker, summary="direct")
    merged_event = await _complete_task(worker, summary="merged")

    direct = inbox.claim_event(
        direct_event,
        owner_id="bridge:test:direct",
        route=CompletionRoute(channel="test", source="source_1"),
    )
    merged_completion = inbox.claim_event(
        merged_event,
        owner_id="bridge:test:merged",
        route=CompletionRoute(channel="test", source="source_1"),
    )
    assert direct is not None
    assert merged_completion is not None

    user = InteractionInbound(channel="test", text="user", source="source_1")
    merged = merge_completion_inbounds(user, [merged_completion])

    assert inbox.ack_from_metadata(direct.metadata) == 1
    assert inbox.ack_from_metadata(merged.metadata) == 1
    assert inbox.ack_from_metadata(direct.metadata) == 0
    assert inbox.ack_from_metadata(merged.metadata) == 0
