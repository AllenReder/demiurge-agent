from demiurge.runtime.control import ActionSource, ActionSpec, EventCursor, EventFilter, RuntimeControlPlane, TaskFilter
from demiurge.runtime.store import RuntimeEvent, RuntimeQuery, RuntimeStore


def test_runtime_store_appends_idempotent_task_event(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    event = RuntimeEvent(
        type="task.submitted",
        aggregate_type="task",
        aggregate_id="task_1",
        payload={"kind": "terminal.exec", "owner_session_id": "session_1"},
    )

    first = store.append([event], idempotency_key="terminal:1")
    second = store.append([event], idempotency_key="terminal:1")

    assert first.first_seq == second.first_seq
    tasks = store.query(RuntimeQuery(table="tasks", where={"task_id": "task_1"})).rows
    assert len(tasks) == 1
    assert tasks[0]["kind"] == "terminal.exec"
    assert tasks[0]["status"] == "queued"


def test_control_plane_submit_control_read_and_stream(tmp_path):
    control = RuntimeControlPlane(RuntimeStore(tmp_path / "runtime.sqlite3"))

    handle = control.submit(
        ActionSpec(kind="agent.spawn", payload={"core_id": "evolver"}),
        source=ActionSource(actor="model", session_id="session_1", turn_id="turn_1", core_id="assistant"),
    )
    record = control.read(handle.task_id, view="debug")
    control.control(handle.task_id, "cancel")
    cancelled = control.read(handle.task_id)
    batch = control.stream(EventCursor(), EventFilter(aggregate_type="task", aggregate_id=handle.task_id))

    assert record["kind"] == "agent.spawn"
    assert cancelled["status"] == "cancelled"
    assert [task["task_id"] for task in control.query(TaskFilter(owner_session_id="session_1"))] == [handle.task_id]
    assert [event["type"] for event in batch.events] == ["task.submitted", "task.cancelled"]


def test_control_plane_projects_non_cancel_task_control(tmp_path):
    control = RuntimeControlPlane(RuntimeStore(tmp_path / "runtime.sqlite3"))
    handle = control.submit(
        ActionSpec(kind="agent.spawn", payload={"task_id": "task_1", "notify_policy": "return_to_parent"}),
        source=ActionSource(actor="model"),
    )

    muted = control.control(handle.task_id, "mute")
    retried = control.control(handle.task_id, "retry")

    assert muted["notify_policy"] == "silent"
    assert retried["status"] == "queued"


def test_runtime_store_projects_blocked_task_status(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    store.append(
        [
            RuntimeEvent(
                type="task.submitted",
                aggregate_type="task",
                aggregate_id="job_1",
                payload={"kind": "agent.spawn", "status": "queued"},
            ),
            RuntimeEvent(
                type="task.started",
                aggregate_type="task",
                aggregate_id="job_1",
                payload={"status": "running"},
            ),
            RuntimeEvent(
                type="task.blocked",
                aggregate_type="task",
                aggregate_id="job_1",
                payload={"status": "blocked_needs_user", "summary": "approval needed"},
            ),
        ]
    )

    task = store.query(RuntimeQuery(table="tasks", where={"task_id": "job_1"}, limit=1)).rows[0]

    assert task["status"] == "blocked_needs_user"
    assert task["started_at"] is not None
    assert task["completed_at"] is None
