import pytest

from demiurge.runtime.completions import CompletionInbox
from demiurge.runtime.control import RuntimeControlPlane
from demiurge.runtime.session import SessionRuntime
from demiurge.runtime.store import RuntimeQuery, RuntimeStore
from demiurge.runtime.tasks import RuntimeTaskWorker
from demiurge.runtime.turn_lifecycle import (
    TurnLifecycleCompletion,
    TurnLifecycleRequest,
    TurnLifecycleRuntime,
)
from demiurge.storage import EventLog


def _runtime(tmp_path):
    home = tmp_path / "home"
    store = RuntimeStore.default(home)
    control_plane = RuntimeControlPlane(store)
    session_runtime = SessionRuntime(control_plane=control_plane)
    task_worker = RuntimeTaskWorker(control_plane=control_plane)
    session_runtime.ensure_session("session_1", core_id="assistant", core_revision="rev_1")
    event_log = EventLog(home, "session_1")
    runtime = TurnLifecycleRuntime(
        home=home,
        session_runtime=session_runtime,
        task_worker=task_worker,
        event_log=event_log,
    )
    return home, store, control_plane, session_runtime, task_worker, event_log, runtime


def _request(**metadata):
    return TurnLifecycleRequest(
        session_id="session_1",
        core_id="assistant",
        core_revision="rev_1",
        raw_text="hello",
        metadata=metadata,
        attachments=({"kind": "file", "path": "a.txt"},),
    )


async def _complete_background_task(worker: RuntimeTaskWorker, *, summary: str):
    async def task(ctx):
        ctx.append_log("line")
        return summary

    record = worker.start_task(
        kind="terminal.exec",
        owner_session_id="session_1",
        owner_turn_id="turn_parent",
        source_tool="terminal",
        task_factory=task,
    )
    await worker.wait(record.task_id, timeout_seconds=1)
    return next(event for event in worker.pending_events_for_session("session_1") if event.task_id == record.task_id)


def test_begin_projects_turn_task_and_inbound_event(tmp_path):
    _, store, control_plane, _, _, event_log, runtime = _runtime(tmp_path)

    lifecycle = runtime.begin(_request(channel="test", source="user_1"))

    assert lifecycle.session_id == "session_1"
    assert lifecycle.turn_id.startswith("turn_")
    assert lifecycle.task_id == lifecycle.turn_id
    assert lifecycle.input_envelope.raw_text == "hello"
    assert lifecycle.input_envelope.attachments == [{"kind": "file", "path": "a.txt"}]
    assert lifecycle.turn.core_id == "assistant"
    assert lifecycle.turn.core_revision == "rev_1"
    assert lifecycle.turn.user_input.content == "hello"

    events = event_log.for_turn(lifecycle.turn_id)
    assert [event["type"] for event in events] == ["turn.started", "message.inbound"]
    assert events[0]["channel"] == "test"
    assert events[1]["content"] == "hello"

    task = control_plane.read(lifecycle.turn_id)
    assert task["kind"] == "agent.turn"
    assert task["status"] == "running"
    assert task["owner_session_id"] == "session_1"
    turns = store.query(RuntimeQuery(table="turns", where={"turn_id": lifecycle.turn_id}, limit=1)).rows
    assert turns[0]["status"] == "running"
    assert turns[0]["task_id"] == lifecycle.turn_id


@pytest.mark.asyncio
async def test_complete_succeeds_task_completes_turn_and_acks_claims(tmp_path):
    _, store, control_plane, _, task_worker, event_log, runtime = _runtime(tmp_path)
    direct_event = await _complete_background_task(task_worker, summary="direct")
    merged_event = await _complete_background_task(task_worker, summary="merged")
    direct_claim = task_worker.claim_pending_event(direct_event.event_id, owner_id="test:direct")
    merged_claim = task_worker.claim_pending_event(merged_event.event_id, owner_id="test:merged")
    assert direct_claim is not None
    assert merged_claim is not None

    lifecycle = runtime.begin(
        _request(
            event_id=direct_event.event_id,
            completion_claim_id=direct_claim.claim_id,
            completion_claims=[{"event_id": merged_event.event_id, "claim_id": merged_claim.claim_id}],
        )
    )
    runtime.complete(
        lifecycle,
        TurnLifecycleCompletion(
            items=({"kind": "delivery", "text": "done"},),
            agent_result={"ok": True},
            needs_user=True,
            result_ref="result:1",
        ),
    )

    completed = event_log.for_turn(lifecycle.turn_id)[-1]
    assert completed["type"] == "turn.completed"
    assert completed["items"] == [{"kind": "delivery", "text": "done"}]
    assert completed["agent_result"] == {"ok": True}
    assert completed["needs_user"] is True
    assert control_plane.read(lifecycle.turn_id)["status"] == "succeeded"
    turns = store.query(RuntimeQuery(table="turns", where={"turn_id": lifecycle.turn_id}, limit=1)).rows
    assert turns[0]["status"] == "completed"
    assert turns[0]["result_ref"] == "result:1"
    assert CompletionInbox(task_worker).ack_from_metadata(lifecycle.metadata) == 0


def test_interrupt_failed_marks_event_session_and_task(tmp_path):
    _, store, control_plane, _, _, event_log, runtime = _runtime(tmp_path)
    lifecycle = runtime.begin(_request())

    runtime.interrupt(lifecycle, status="failed", error="RuntimeError: boom")

    failed = event_log.for_turn(lifecycle.turn_id)[-1]
    assert failed["type"] == "turn.failed"
    assert failed["error"] == "RuntimeError: boom"
    task = control_plane.read(lifecycle.turn_id)
    assert task["status"] == "failed"
    assert task["error"]["message"] == "RuntimeError: boom"
    turns = store.query(RuntimeQuery(table="turns", where={"turn_id": lifecycle.turn_id}, limit=1)).rows
    assert turns[0]["status"] == "failed"
    assert turns[0]["result_ref"] == lifecycle.turn_id


def test_interrupt_cancelled_marks_event_session_and_task(tmp_path):
    _, store, control_plane, _, _, event_log, runtime = _runtime(tmp_path)
    lifecycle = runtime.begin(_request())

    runtime.interrupt(lifecycle, status="cancelled", error="turn cancelled")

    cancelled = event_log.for_turn(lifecycle.turn_id)[-1]
    assert cancelled["type"] == "turn.cancelled"
    assert cancelled["error"] == "turn cancelled"
    task = control_plane.read(lifecycle.turn_id)
    assert task["status"] == "cancelled"
    assert task["error"]["message"] == "turn cancelled"
    turns = store.query(RuntimeQuery(table="turns", where={"turn_id": lifecycle.turn_id}, limit=1)).rows
    assert turns[0]["status"] == "cancelled"
    assert turns[0]["result_ref"] == lifecycle.turn_id
