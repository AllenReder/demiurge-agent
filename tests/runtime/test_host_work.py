from __future__ import annotations

from datetime import UTC, datetime

import pytest

from demiurge.runtime.control import RuntimeControlPlane
from demiurge.runtime.host_work import HostWorkLifecycleRuntime
from demiurge.runtime.session import SessionRuntime
from demiurge.runtime.store import RuntimeEvent, RuntimeStore
from demiurge.runtime.tasks import RuntimeTaskWorker


def test_host_work_status_merges_delivery_work_and_outbox(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    control = RuntimeControlPlane(store)
    sessions = SessionRuntime(control_plane=control)
    sessions.ensure_session(
        "session_1",
        core_id="assistant",
        core_revision="0001",
        channel="tui",
        conversation_key="local",
    )
    sessions.append_delivery_message(
        "session_1",
        role="assistant",
        content="hello",
        turn_id="turn_1",
        delivery_id="delivery_1",
        channel="tui",
        target={"conversation_key": "local"},
        delivery_payload={"fallback_text": "hello"},
    )
    lifecycle = HostWorkLifecycleRuntime(store=store)

    queued = lifecycle.status("delivery_1")
    claim = lifecycle.claim_delivery("delivery_1", owner_id="test.delivery")
    assert claim is not None
    lifecycle.mark_delivery_sending(claim)
    sending = lifecycle.status("delivery_1")
    session_work = lifecycle.list_session_work("session_1")
    event_types = [event.type for event in lifecycle.list_events(work_id="delivery_1")]

    assert queued.kind == "delivery.send"
    assert queued.status == "queued"
    assert sending.status == "sending"
    assert sending.work_status == "sending"
    assert sending.delivery_id == "delivery_1"
    assert sending.owner_session_id == "session_1"
    assert [item.work_id for item in session_work] == ["delivery_1"]
    assert event_types == ["delivery.queued", "work.enqueued", "work.claimed", "work.sending"]


@pytest.mark.asyncio
async def test_host_work_observes_and_acknowledges_task_completion_work(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    control = RuntimeControlPlane(store)
    lifecycle = HostWorkLifecycleRuntime(store=store)
    worker = RuntimeTaskWorker(control_plane=control, host_work=lifecycle, log_tail_lines=3)

    async def task(ctx):
        ctx.append_log("line")
        return "done"

    record = worker.start_task(
        kind="terminal.exec",
        owner_session_id="session_1",
        owner_turn_id="turn_1",
        source_tool="test_tool",
        task_factory=task,
    )
    await worker.wait(record.task_id, timeout_seconds=1)
    completion = worker.pending_events_for_session("session_1")[0]

    item = lifecycle.status(completion.event_id)
    claim = lifecycle.claim_task_completion(completion.event_id, owner_id="test.completion")
    assert claim is not None
    lifecycle.acknowledge_task_completion(claim)
    acknowledged = lifecycle.status(completion.event_id)
    task_events = [event.type for event in lifecycle.list_events(task_id=record.task_id)]

    assert item.kind == "task.completion"
    assert item.status == "queued"
    assert item.task_id == record.task_id
    assert item.task_status == "succeeded"
    assert item.summary == "done"
    assert item.log_tail == ("line",)
    assert acknowledged.status == "acknowledged"
    assert worker.pending_events_for_session("session_1") == []
    assert task_events == [
        "task.submitted",
        "task.started",
        "task.log",
        "task.succeeded",
        "task.completion_ready",
        "work.enqueued",
        "work.claimed",
        "work.acknowledged",
        "task.completion_acknowledged",
    ]


@pytest.mark.asyncio
async def test_host_work_session_summary_excludes_non_host_task_rows(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    control = RuntimeControlPlane(store)
    lifecycle = HostWorkLifecycleRuntime(store=store)
    worker = RuntimeTaskWorker(control_plane=control, host_work=lifecycle)
    store.append(
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

    async def task(ctx):
        return "done"

    record = worker.start_task(
        kind="terminal.exec",
        owner_session_id="session_1",
        owner_turn_id="turn_1",
        source_tool="test_tool",
        task_factory=task,
        notify_on_complete=False,
    )
    await worker.wait(record.task_id, timeout_seconds=1)

    items = lifecycle.list_session_work("session_1")

    assert [(item.work_id, item.kind) for item in items] == [(record.task_id, "terminal.exec")]


def test_host_work_status_merges_schedule_fire_projection(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    lifecycle = HostWorkLifecycleRuntime(store=store)
    due_at = "2026-06-28T10:00:00Z"
    work_id = f"schedule:assistant:daily:{due_at}"
    lifecycle.enqueue_schedule_fire(
        work_id,
        core_id="assistant",
        schedule_id="daily",
        due_at=due_at,
        next_attempt_at=due_at,
        now=datetime(2026, 6, 28, 9, 59, tzinfo=UTC),
    )
    store.append(
        [
            RuntimeEvent(
                type="scheduler.scheduled",
                aggregate_type="scheduler_instance",
                aggregate_id=f"assistant:daily:{due_at}",
                payload={
                    "core_id": "assistant",
                    "schedule_id": "daily",
                    "due_at": due_at,
                    "task_id": None,
                    "claim_status": "scheduled",
                    "idempotency_key": "test",
                },
            )
        ]
    )

    item = lifecycle.status(work_id)
    event_types = [event.type for event in lifecycle.list_events(work_id=work_id)]

    assert item.kind == "schedule.fire"
    assert item.status == "scheduled"
    assert item.work_status == "queued"
    assert item.schedule_id == "daily"
    assert item.details["scheduler"]["core_id"] == "assistant"
    assert event_types == ["work.enqueued", "scheduler.scheduled"]
