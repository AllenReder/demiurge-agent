import asyncio

import pytest

from demiurge.runtime.tasks import RuntimeTaskConflictError, RuntimeTaskWorker
from demiurge.runtime.control import RuntimeControlPlane
from demiurge.runtime.store import RuntimeStore


@pytest.mark.asyncio
async def test_task_worker_lifecycle_default_notify_and_log_tail(tmp_path):
    control = RuntimeControlPlane(RuntimeStore(tmp_path / "runtime.sqlite3"))
    runtime = RuntimeTaskWorker(log_tail_lines=2, log_tail_chars=80, control_plane=control)
    events = []
    runtime.subscribe(events.append)

    async def task(ctx):
        ctx.append_log("one")
        ctx.append_log("two")
        ctx.append_log("three")
        return "done"

    record = runtime.start_task(
        backend="test",
        owner_session_id="session_1",
        owner_turn_id="turn_1",
        source_tool="test_tool",
        task_factory=task,
    )

    record = await runtime.wait(record.job_id, timeout_seconds=1)

    assert record.status == "succeeded"
    assert record.summary == "done"
    assert record.log_tail == ["two", "three"]
    assert len(events) == 1
    assert events[0].job_id == record.job_id
    assert events[0].owner_session_id == "session_1"
    assert runtime.pending_events_for_session("session_1")[0].job_id == record.job_id
    recovered = RuntimeTaskWorker(log_tail_lines=2, log_tail_chars=80, control_plane=control)
    recovered_event = recovered.pending_events_for_session("session_1")[0]
    assert recovered_event.job_id == record.job_id
    assert recovered.clear_pending_event(recovered_event.event_id) is True
    assert recovered.pending_events_for_session("session_1") == []
    task = control.read(record.job_id, view="debug")
    assert task["kind"] == "tool.call"
    assert task["status"] == "succeeded"
    assert [line["text"] for line in task["logs"]] == ["one", "two", "three"]
    assert [event["type"] for event in task["events"]] == [
        "task.submitted",
        "task.started",
        "task.log",
        "task.log",
        "task.log",
        "task.succeeded",
    ]


@pytest.mark.asyncio
async def test_task_worker_cancel_and_write_scope_conflict(tmp_path):
    control = RuntimeControlPlane(RuntimeStore(tmp_path / "runtime.sqlite3"))
    runtime = RuntimeTaskWorker(control_plane=control)
    release = asyncio.Event()

    async def task(ctx):
        await release.wait()

    record = runtime.start_task(
        backend="test",
        owner_session_id="session_1",
        owner_turn_id="turn_1",
        source_tool="test_tool",
        task_factory=task,
        write_scope="scope:a",
    )

    with pytest.raises(RuntimeTaskConflictError):
        runtime.start_task(
            backend="test",
            owner_session_id="session_1",
            owner_turn_id="turn_2",
            source_tool="test_tool",
            task_factory=task,
            write_scope="scope:a",
        )

    cancelled = await runtime.cancel(record.job_id)

    assert cancelled.status == "cancelled"
    assert cancelled.running is False


@pytest.mark.asyncio
async def test_task_worker_mark_blocked_notifies_without_completion(tmp_path):
    control = RuntimeControlPlane(RuntimeStore(tmp_path / "runtime.sqlite3"))
    runtime = RuntimeTaskWorker(control_plane=control)
    events = []
    runtime.subscribe(events.append)

    async def task(ctx):
        ctx.mark_blocked("approval needed", metadata={"approval": True})

    record = runtime.start_task(
        backend="test",
        owner_session_id="session_1",
        owner_turn_id="turn_1",
        source_tool="test_tool",
        task_factory=task,
    )

    await runtime.wait(record.job_id, timeout_seconds=1)

    assert record.status == "blocked_needs_user"
    assert record.summary == "approval needed"
    assert events[0].status == "blocked_needs_user"
    task = control.read(record.job_id, view="debug")
    assert task["status"] == "blocked_needs_user"
    assert task["completed_at"] is None
    assert [event["type"] for event in task["events"]] == ["task.submitted", "task.started", "task.blocked"]
