from dataclasses import replace

import pytest

from demiurge.runtime.completions import (
    CompletionInbox,
    CompletionRoute,
    is_background_completion,
    merge_completion_inbounds,
)
from demiurge.runtime.control import RuntimeControlPlane
from demiurge.runtime.interactions import InteractionInbound
from demiurge.runtime.scope import PrincipalScopeResolver
from demiurge.runtime.session import SessionRuntime
from demiurge.runtime.store import RuntimeStore
from demiurge.runtime.tasks import RuntimeTaskWorker
from tests.runtime.operator_authority_support import activate_test_operator_authority


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
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    control = RuntimeControlPlane(store)
    sessions = SessionRuntime(control_plane=control)
    resolver = PrincipalScopeResolver(store)
    scope = resolver.issue_conversation(
        channel="test",
        principal_key="principal_1",
        conversation_key="conversation_1",
        session_id="session_1",
    )
    sessions.create_session(
        session_id="session_1",
        core_id="assistant",
        core_revision="rev",
        principal_scope=scope,
    )
    scope = resolver.conversation(
        channel="test",
        principal_key="principal_1",
        conversation_key="conversation_1",
        session_id="session_1",
    )
    worker = RuntimeTaskWorker(control_plane=control)
    worker.bind_turn_scope(session_id="session_1", turn_id="turn_1", scope=scope)
    return worker


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
            principal_key="principal_1",
            reply_to="reply_1",
            conversation_key="conversation_1",
            metadata={"route": "kept"},
        ),
    )

    assert inbound is not None
    assert is_background_completion(inbound)
    assert inbound.channel == "test"
    assert inbound.source == "source_1"
    assert inbound.principal_key is None
    assert inbound.reply_to == "reply_1"
    assert inbound.conversation_key == "conversation_1"
    assert inbound.metadata["route"] == "kept"
    assert inbound.metadata["event_id"] == event.event_id
    assert inbound.metadata["task_id"] == event.task_id
    assert inbound.metadata["completion_claim_id"]
    assert "[SYSTEM: Background task event]" in inbound.text
    assert "background complete" in inbound.text


@pytest.mark.asyncio
async def test_unscoped_completion_fails_closed_before_claim(tmp_path):
    worker = RuntimeTaskWorker(
        control_plane=RuntimeControlPlane(RuntimeStore(tmp_path / "runtime.sqlite3"))
    )
    event = await _complete_task(worker)

    with pytest.raises(PermissionError, match="no persisted origin"):
        CompletionInbox(worker).claim_event(
            event,
            owner_id="bridge:test",
            route=CompletionRoute(
                channel="webhook",
                source="forged",
                principal_key="forged",
                conversation_key="webhook:forged",
            ),
        )

    assert [pending.event_id for pending in worker.pending_events_for_session("session_1")] == [
        event.event_id
    ]


@pytest.mark.asyncio
async def test_completion_restores_captured_origin_scope_without_route_elevation(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    sessions = SessionRuntime(control_plane=RuntimeControlPlane(store))
    resolver = PrincipalScopeResolver(store)
    scope = resolver.issue_conversation(
        channel="slack",
        principal_key="team:T1:channel:C1",
        conversation_key="slack:channel:T1:C1",
        session_id="session_1",
    )
    sessions.create_session(
        session_id="session_1",
        core_id="assistant",
        core_revision="rev",
        channel="slack",
        conversation_key="slack:channel:T1:C1",
        principal_scope=scope,
    )
    scope = resolver.conversation(
        channel="slack",
        principal_key="team:T1:channel:C1",
        conversation_key="slack:channel:T1:C1",
        session_id="session_1",
    )
    worker = RuntimeTaskWorker(control_plane=RuntimeControlPlane(store))
    worker.bind_turn_scope(session_id="session_1", turn_id="turn_1", scope=scope)
    try:
        event = await _complete_task(worker)
    finally:
        worker.release_turn_scope(session_id="session_1", turn_id="turn_1")

    assert event.origin_scope_record is not None
    assert event.origin_scope_record["allowed_session_ids"] == ["session_1"]
    assert "principal_scope" not in event.to_metadata()
    assert "origin_scope" not in event.to_metadata()

    inbound = CompletionInbox(worker).claim_event(
        event,
        owner_id="bridge:test",
        route=CompletionRoute(
            channel="webhook",
            source="forged-source",
            principal_key="forged-principal",
            conversation_key="webhook:forged",
        ),
    )

    assert inbound is not None
    assert inbound.principal_scope == scope
    assert inbound.channel == "slack"
    assert inbound.conversation_key == "slack:channel:T1:C1"
    assert "principal_scope" not in inbound.metadata
    assert "origin_scope" not in inbound.metadata


@pytest.mark.asyncio
async def test_completion_owner_mismatch_fails_before_claim(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    sessions = SessionRuntime(control_plane=RuntimeControlPlane(store))
    resolver = PrincipalScopeResolver(store)
    scope = resolver.issue_conversation(
        channel="matrix",
        principal_key="@alice:example.com",
        conversation_key="matrix:room:%21a%3Aexample.com",
        session_id="session_1",
    )
    sessions.create_session(
        session_id="session_1",
        core_id="assistant",
        core_revision="rev",
        principal_scope=scope,
    )
    scope = resolver.conversation(
        channel="matrix",
        principal_key="@alice:example.com",
        conversation_key="matrix:room:%21a%3Aexample.com",
        session_id="session_1",
    )
    worker = RuntimeTaskWorker(control_plane=RuntimeControlPlane(store))
    worker.bind_turn_scope(session_id="session_1", turn_id="turn_1", scope=scope)
    try:
        event = await _complete_task(worker)
    finally:
        worker.release_turn_scope(session_id="session_1", turn_id="turn_1")
    forged = replace(event, owner_session_id="session_other")

    with pytest.raises(PermissionError, match="owner session"):
        CompletionInbox(worker).claim_event(
            forged,
            owner_id="bridge:test",
            route=CompletionRoute(channel="matrix", source="source"),
        )

    assert [pending.event_id for pending in worker.pending_events_for_session("session_1")] == [
        event.event_id
    ]


@pytest.mark.asyncio
async def test_explicit_operator_completion_can_target_conversation_owned_session(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    activate_test_operator_authority(store)
    sessions = SessionRuntime(control_plane=RuntimeControlPlane(store))
    resolver = PrincipalScopeResolver(store)
    owner_scope = resolver.issue_conversation(
        channel="slack",
        principal_key="team:T1:channel:C1",
        conversation_key="slack:channel:T1:C1",
        session_id="session_1",
    )
    sessions.create_session(
        session_id="session_1",
        core_id="assistant",
        core_revision="rev",
        channel="slack",
        conversation_key="slack:channel:T1:C1",
        principal_scope=owner_scope,
    )
    operator_scope = resolver.local_operator(
        active_session_id="session_1",
        reason="operate on conversation-owned session",
    )
    worker = RuntimeTaskWorker(control_plane=RuntimeControlPlane(store))

    worker.bind_turn_scope(
        session_id="session_1",
        turn_id="turn_1",
        scope=operator_scope,
    )
    try:
        event = await _complete_task(worker)
    finally:
        worker.release_turn_scope(session_id="session_1", turn_id="turn_1")
    inbound = CompletionInbox(worker).claim_event(
        event,
        owner_id="bridge:test",
        route=CompletionRoute(channel="tui", source="local"),
    )

    assert inbound is not None
    assert inbound.principal_scope is not None
    assert inbound.principal_scope.authority.value == "operator"
    assert inbound.principal_scope.allowed_session_ids == frozenset({"session_1"})
    assert inbound.channel == "tui"


def test_merge_completion_inbounds_preserves_user_route_and_records_claims():
    user = InteractionInbound(
        channel="test",
        text="user message",
        source="source_1",
        principal_key="principal_1",
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
    assert merged.principal_key == user.principal_key
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
