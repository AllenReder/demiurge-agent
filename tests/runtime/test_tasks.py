import asyncio

import pytest

from demiurge.runtime.tasks import (
    RuntimeTaskConflictError,
    RuntimeTaskKindError,
    RuntimeTaskWorker,
    RuntimeTaskWorkerClosedError,
)
from demiurge.runtime.control import RuntimeControlPlane
from demiurge.runtime.store import RuntimeEvent, RuntimeQuery, RuntimeStore


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
        kind="terminal.exec",
        owner_session_id="session_1",
        owner_turn_id="turn_1",
        source_tool="test_tool",
        task_factory=task,
    )

    record = await runtime.wait(record.task_id, timeout_seconds=1)

    assert record.status == "succeeded"
    assert record.summary == "done"
    assert record.log_tail == ["two", "three"]
    assert len(events) == 1
    assert events[0].task_id == record.task_id
    assert events[0].owner_session_id == "session_1"
    assert runtime.pending_events_for_session("session_1")[0].task_id == record.task_id
    recovered = RuntimeTaskWorker(log_tail_lines=2, log_tail_chars=80, control_plane=control)
    recovered_event = recovered.pending_events_for_session("session_1")[0]
    assert recovered_event.task_id == record.task_id
    claim = recovered.claim_pending_event(recovered_event.event_id, owner_id="test")
    assert claim is not None
    assert recovered.ack_pending_event(claim) is True
    assert recovered.pending_events_for_session("session_1") == []
    task = control.read(record.task_id, view="debug")
    assert task["kind"] == "terminal.exec"
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
        kind="terminal.exec",
        owner_session_id="session_1",
        owner_turn_id="turn_1",
        source_tool="test_tool",
        task_factory=task,
        write_scope="scope:a",
    )

    with pytest.raises(RuntimeTaskConflictError):
        runtime.start_task(
            kind="terminal.exec",
            owner_session_id="session_1",
            owner_turn_id="turn_2",
            source_tool="test_tool",
            task_factory=task,
            write_scope="scope:a",
        )

    cancelled = await runtime.cancel(record.task_id)

    assert cancelled.status == "cancelled"
    assert cancelled.running is False


@pytest.mark.asyncio
async def test_task_worker_cancel_cleanup_failure_is_terminal_and_observable(tmp_path):
    control = RuntimeControlPlane(RuntimeStore(tmp_path / "runtime.sqlite3"))
    runtime = RuntimeTaskWorker(control_plane=control)
    release = asyncio.Event()

    async def task(ctx):
        async def fail_cleanup():
            raise RuntimeError("synthetic cleanup failure")

        ctx.set_cancel_callback(fail_cleanup)
        await release.wait()

    record = runtime.start_task(
        kind="terminal.exec",
        owner_session_id="session_1",
        owner_turn_id="turn_1",
        source_tool="test_tool",
        task_factory=task,
    )
    await asyncio.sleep(0)

    cancelled = await runtime.cancel(record.task_id)

    assert cancelled.status == "failed"
    assert cancelled.running is False
    assert cancelled.metadata["cancel_cleanup_error_type"] == "RuntimeError"
    task_view = control.read(record.task_id, view="debug")
    assert [event["type"] for event in task_view["events"]][-1] == "task.failed"
    assert runtime.active_count == 0


@pytest.mark.asyncio
async def test_task_worker_concurrent_cancel_runs_cleanup_once(tmp_path):
    control = RuntimeControlPlane(RuntimeStore(tmp_path / "runtime.sqlite3"))
    runtime = RuntimeTaskWorker(control_plane=control)
    cleanup_started = asyncio.Event()
    release_cleanup = asyncio.Event()
    cleanup_calls = 0

    async def task(ctx):
        async def cleanup():
            nonlocal cleanup_calls
            cleanup_calls += 1
            cleanup_started.set()
            if cleanup_calls > 1:
                raise RuntimeError("duplicate cleanup")
            await release_cleanup.wait()

        ctx.set_cancel_callback(cleanup)
        await asyncio.Event().wait()

    record = runtime.start_task(
        kind="terminal.exec",
        owner_session_id="session_1",
        owner_turn_id="turn_1",
        source_tool="test_tool",
        task_factory=task,
    )
    await asyncio.sleep(0)

    first_cancel = asyncio.create_task(runtime.cancel(record.task_id))
    await cleanup_started.wait()
    second_cancel = asyncio.create_task(runtime.cancel(record.task_id))
    await asyncio.sleep(0)
    release_cleanup.set()
    first, second = await asyncio.gather(first_cancel, second_cancel)

    assert cleanup_calls == 1
    assert first.status == second.status == "cancelled"
    runtime_events = control.store.query(
        RuntimeQuery(table="runtime_events", order_by="seq", limit=100)
    ).rows
    terminal_events = [
        event["type"]
        for event in runtime_events
        if (
            event["type"] in {"task.cancelled", "task.failed", "task.completion_ready"}
            and (
                event["aggregate_id"] == record.task_id
                or event["payload"].get("task_id") == record.task_id
            )
        )
    ]
    assert terminal_events == ["task.cancelled", "task.completion_ready"]


@pytest.mark.asyncio
async def test_task_worker_failure_is_terminal_when_error_log_persistence_fails(monkeypatch, tmp_path):
    control = RuntimeControlPlane(RuntimeStore(tmp_path / "runtime.sqlite3"))
    runtime = RuntimeTaskWorker(control_plane=control)

    async def task(ctx):
        raise ValueError("synthetic task failure")

    def fail_log_persistence(task_id, text):
        raise RuntimeError("synthetic log persistence failure")

    monkeypatch.setattr(runtime, "append_log", fail_log_persistence)
    record = runtime.start_task(
        kind="terminal.exec",
        owner_session_id="session_1",
        owner_turn_id="turn_1",
        source_tool="test_tool",
        task_factory=task,
    )

    failed = await runtime.wait(record.task_id, timeout_seconds=1)

    assert failed.status == "failed"
    assert failed.metadata["error_log_persist_error_type"] == "RuntimeError"
    task_view = control.read(record.task_id, view="debug")
    assert task_view["status"] == "failed"
    assert task_view["events"][-1]["type"] == "task.failed"


@pytest.mark.asyncio
async def test_task_worker_rejects_new_task_after_shutdown(tmp_path):
    control = RuntimeControlPlane(RuntimeStore(tmp_path / "runtime.sqlite3"))
    runtime = RuntimeTaskWorker(control_plane=control)
    task_started = asyncio.Event()
    shutdown_started = asyncio.Event()
    release_shutdown = asyncio.Event()

    async def active_task(ctx):
        async def cancel_active_task():
            shutdown_started.set()
            await release_shutdown.wait()

        ctx.set_cancel_callback(cancel_active_task)
        task_started.set()
        await asyncio.Event().wait()

    runtime.start_task(
        kind="terminal.exec",
        owner_session_id="session_1",
        owner_turn_id="turn_1",
        source_tool="test_tool",
        task_factory=active_task,
    )
    await task_started.wait()
    shutdown_task = asyncio.create_task(runtime.shutdown())
    await shutdown_started.wait()

    async def rejected_task(ctx):
        return None

    with pytest.raises(RuntimeTaskWorkerClosedError):
        runtime.start_task(
            kind="terminal.exec",
            owner_session_id="session_1",
            owner_turn_id="turn_1",
            source_tool="test_tool",
            task_factory=rejected_task,
        )

    release_shutdown.set()
    await shutdown_task

    with pytest.raises(RuntimeTaskWorkerClosedError):
        runtime.start_task(
            kind="terminal.exec",
            owner_session_id="session_1",
            owner_turn_id="turn_1",
            source_tool="test_tool",
            task_factory=rejected_task,
        )


@pytest.mark.asyncio
async def test_task_worker_mark_blocked_notifies_without_completion(tmp_path):
    control = RuntimeControlPlane(RuntimeStore(tmp_path / "runtime.sqlite3"))
    runtime = RuntimeTaskWorker(control_plane=control)
    events = []
    runtime.subscribe(events.append)

    async def task(ctx):
        ctx.mark_blocked("approval needed", metadata={"approval": True})

    record = runtime.start_task(
        kind="agent.spawn",
        owner_session_id="session_1",
        owner_turn_id="turn_1",
        source_tool="test_tool",
        task_factory=task,
    )

    await runtime.wait(record.task_id, timeout_seconds=1)

    assert record.status == "blocked_needs_user"
    assert record.summary == "approval needed"
    assert events[0].status == "blocked_needs_user"
    task = control.read(record.task_id, view="debug")
    assert task["status"] == "blocked_needs_user"
    assert task["completed_at"] is None
    assert [event["type"] for event in task["events"]] == ["task.submitted", "task.started", "task.blocked"]


def test_task_worker_lists_background_tasks_only_and_fails_unknown_kind(tmp_path):
    control = RuntimeControlPlane(RuntimeStore(tmp_path / "runtime.sqlite3"))
    control.store.append(
        [
            RuntimeEvent(
                type="task.submitted",
                aggregate_type="task",
                aggregate_id="tool_1",
                actor={"actor": "host.tool_runtime", "session_id": "session_1", "turn_id": "turn_1"},
                payload={"kind": "tool.call", "owner_session_id": "session_1", "status": "queued"},
            )
        ]
    )
    runtime = RuntimeTaskWorker(control_plane=control)

    assert runtime.list_tasks(owner_session_id="session_1") == []
    with pytest.raises(RuntimeTaskKindError):
        runtime.list_tasks(kind="tool.call")
    with pytest.raises(RuntimeTaskKindError):
        runtime.start_task(
            kind="tool.call",
            owner_session_id="session_1",
            owner_turn_id="turn_1",
            source_tool="test_tool",
            task_factory=lambda ctx: asyncio.sleep(0),
        )


@pytest.mark.asyncio
async def test_task_completion_ack_requires_inflight_claim(tmp_path):
    control = RuntimeControlPlane(RuntimeStore(tmp_path / "runtime.sqlite3"))
    runtime = RuntimeTaskWorker(control_plane=control)

    async def task(ctx):
        return "done"

    record = runtime.start_task(
        kind="terminal.exec",
        owner_session_id="session_1",
        owner_turn_id="turn_1",
        source_tool="test_tool",
        task_factory=task,
    )
    await runtime.wait(record.task_id, timeout_seconds=1)
    event = runtime.pending_events_for_session("session_1")[0]

    inflight = runtime.claim_pending_event(event.event_id, owner_id="bridge:text")
    assert inflight is not None
    assert runtime.pending_events_for_session("session_1") == []
    assert runtime.ack_pending_event(inflight) is True
    assert runtime.pending_events_for_session("session_1") == []
