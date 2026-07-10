from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from baseline_support import BaselineContractFailure
from demiurge import util
from demiurge.runtime.control import RuntimeControlPlane
from demiurge.runtime.session import SessionRuntime
from demiurge.runtime.store import AppendResult, ProjectionPage, RuntimeEvent, RuntimeQuery, RuntimeStore
from demiurge.runtime.tasks import RuntimeTaskWorker


pytestmark = pytest.mark.stress


class CountingRuntimeStore(RuntimeStore):
    def __init__(self, path):
        self.task_log_query_count = 0
        self.task_log_rows_materialized = 0
        super().__init__(path)

    def query(self, query: RuntimeQuery) -> ProjectionPage:
        page = super().query(query)
        if query.table == "task_logs":
            self.task_log_query_count += 1
            self.task_log_rows_materialized += len(page.rows)
        return page


def _submit_task(store: RuntimeStore, task_id: str = "task_baseline") -> None:
    store.append(
        [
            RuntimeEvent(
                type="task.submitted",
                aggregate_type="task",
                aggregate_id=task_id,
                event_id=f"evt_submit_{task_id}",
                payload={
                    "kind": "terminal.exec",
                    "status": "running",
                    "owner_session_id": "session_baseline",
                    "owner_turn_id": "turn_baseline",
                    "source": {"actor": "stress.baseline"},
                },
            )
        ]
    )


@pytest.mark.xfail(
    strict=True,
    raises=BaselineContractFailure,
    reason="LOG-01: append_log rebuilds the persisted tail twice and max_log_lines is unused",
)
def test_log_01_incremental_append_work_is_bounded_by_retained_window(tmp_path, baseline_recorder):
    append_count = 64
    retained_window = 16
    store = CountingRuntimeStore(tmp_path / "runtime.sqlite3")
    _submit_task(store)
    worker = RuntimeTaskWorker(
        control_plane=RuntimeControlPlane(store),
        max_log_lines=retained_window,
        log_tail_lines=8,
    )

    with baseline_recorder.measure(
        "runtime_task_log_incremental_append",
        finding="LOG-01",
        scale={"append_count": append_count, "retained_window": retained_window},
    ) as sample:
        for index in range(append_count):
            worker.append_log("task_baseline", f"line-{index:06d}")
        sample.observations.update(
            {
                "task_log_queries": store.task_log_query_count,
                "rows_materialized": store.task_log_rows_materialized,
            }
        )
        sample.require(
            store.task_log_query_count <= append_count + 2
            and store.task_log_rows_materialized <= append_count * retained_window * 2,
            "each append must do at most one bounded retained-window log read",
        )


@pytest.mark.xfail(
    strict=True,
    raises=BaselineContractFailure,
    reason="LOG-01: max_log_lines does not currently bound persisted task logs",
)
def test_log_01_max_log_lines_bounds_persisted_rows(tmp_path, baseline_recorder):
    retained_window = 16
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    _submit_task(store)
    worker = RuntimeTaskWorker(
        control_plane=RuntimeControlPlane(store),
        max_log_lines=retained_window,
    )

    with baseline_recorder.measure(
        "runtime_task_log_retention",
        finding="LOG-01",
        scale={"append_count": retained_window + 1, "retained_window": retained_window},
    ) as sample:
        for index in range(retained_window + 1):
            worker.append_log("task_baseline", f"line-{index:06d}")
        rows = store.query(
            RuntimeQuery(
                table="task_logs",
                where={"task_id": "task_baseline"},
                order_by="seq",
                limit=retained_window + 2,
            )
        ).rows
        sample.observations["persisted_rows"] = len(rows)
        sample.require(
            len(rows) <= retained_window,
            "persisted task-log rows must not exceed max_log_lines",
        )


@pytest.mark.xfail(
    strict=True,
    raises=BaselineContractFailure,
    reason="ID-01: utc_id truncates distinct UUID values to the same 32-bit suffix",
)
def test_id_01_runtime_ids_preserve_distinct_uuid_entropy(monkeypatch, baseline_recorder):
    values = iter(
        [
            type("UUIDValue", (), {"hex": "deadbeef000000000000000000000001"})(),
            type("UUIDValue", (), {"hex": "deadbeefffffffffffffffffffffffff"})(),
        ]
    )
    monkeypatch.setattr(util.uuid, "uuid4", lambda: next(values))
    monkeypatch.setattr(util.time, "strftime", lambda *args, **kwargs: "20260101T000000Z")

    with baseline_recorder.measure(
        "runtime_id_collision_entropy",
        finding="ID-01",
        scale={"ids": 2, "shared_uuid_prefix_hex_chars": 8},
    ) as sample:
        first = util.utc_id("evt_")
        second = util.utc_id("evt_")
        sample.observations.update(
            {
                "first": first,
                "second": second,
                "unique": first != second,
            }
        )
        sample.require(first != second, "distinct UUID values must produce distinct runtime ids")


class VirtualCapacityStore:
    """O(1) store instrumentation for exercising very large worker append counts."""

    def __init__(self) -> None:
        self.log_lines = 0
        self.task_log_query_count = 0
        self.virtual_rows_scanned = 0

    def append(self, events, *, idempotency_key=None, expected=None) -> AppendResult:
        del idempotency_key, expected
        materialized = list(events)
        self.log_lines += sum(1 for event in materialized if event.type == "task.log")
        return AppendResult(events=(), first_seq=None, last_seq=None)

    def query(self, query: RuntimeQuery) -> ProjectionPage:
        if query.table == "tasks":
            rows = (
                {
                    "task_id": "task_virtual",
                    "kind": "terminal.exec",
                    "status": "running",
                    "owner_session_id": "session_virtual",
                    "owner_turn_id": "turn_virtual",
                    "source": {},
                    "notify_policy": "silent",
                },
            )
        elif query.table == "runtime_events":
            rows = (
                {
                    "type": "task.submitted",
                    "payload": {"action": {"source_tool": "stress.baseline"}},
                },
            )
        elif query.table == "task_logs":
            self.task_log_query_count += 1
            self.virtual_rows_scanned += min(self.log_lines, query.limit)
            rows = () if self.log_lines == 0 else ({"text": f"line-{self.log_lines - 1:06d}"},)
        else:
            rows = ()
        return ProjectionPage(rows=rows, limit=query.limit, offset=query.offset)


@pytest.mark.parametrize("append_count", [10_000, 100_000])
@pytest.mark.xfail(
    strict=True,
    raises=BaselineContractFailure,
    reason="LOG-01: RuntimeTaskWorker append work scales with total history instead of retained window",
)
def test_log_01_worker_append_capacity_uses_bounded_work(
    baseline_recorder,
    append_count,
):
    retained_window = 4_000
    store = VirtualCapacityStore()
    worker = RuntimeTaskWorker(
        control_plane=RuntimeControlPlane(store),
        max_log_lines=retained_window,
        log_tail_lines=8,
    )

    with baseline_recorder.measure(
        "runtime_task_worker_append_capacity",
        finding="LOG-01",
        scale={"append_count": append_count, "retained_window": retained_window},
    ) as sample:
        for index in range(append_count):
            worker.append_log("task_virtual", f"line-{index:06d}")
        sample.observations.update(
            {
                "append_calls": append_count,
                "persisted_log_lines": store.log_lines,
                "task_log_queries": store.task_log_query_count,
                "virtual_rows_scanned": store.virtual_rows_scanned,
            }
        )
        sample.require(
            store.task_log_query_count <= append_count + 2
            and store.virtual_rows_scanned <= append_count * retained_window,
            "10k/100k worker append must remain linear in the retained window",
        )


@pytest.mark.parametrize("line_count", [10_005, 100_005])
@pytest.mark.xfail(
    strict=True,
    raises=BaselineContractFailure,
    reason="STORE-01: task-log tail freezes at the first 10k rows",
)
def test_task_log_persisted_tail_capacity_baseline(tmp_path, baseline_recorder, line_count):
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    control = RuntimeControlPlane(store)
    _submit_task(store)
    worker = RuntimeTaskWorker(control_plane=control, max_log_lines=4_000, log_tail_lines=8)
    batch_size = 5_000

    with baseline_recorder.measure(
        "runtime_task_log_append_and_tail",
        finding=["LOG-01", "STORE-01"],
        scale={"input_lines": line_count, "batch_size": batch_size, "configured_max_log_lines": 4_000},
    ) as sample:
        for start in range(0, line_count, batch_size):
            stop = min(start + batch_size, line_count)
            store.append(
                [
                    RuntimeEvent(
                        type="task.log",
                        aggregate_type="task",
                        aggregate_id="task_baseline",
                        event_id=f"evt_log_{index:06d}",
                        payload={"stream": "stdout", "text": f"line-{index:06d}"},
                    )
                    for index in range(start, stop)
                ]
            )
        persisted = store.query(
            RuntimeQuery(
                table="task_logs",
                where={"task_id": "task_baseline"},
                order_by="seq",
                limit=line_count + 1,
            )
        ).rows
        tail = worker.log("task_baseline", tail=8)
        expected_newest = f"line-{line_count - 1:06d}"
        sample.observations.update(
            {
                "persisted_rows": len(persisted),
                "tail_lines": len(tail),
                "tail_newest": tail[-1] if tail else None,
                "expected_newest": expected_newest,
                "newest_visible": bool(tail and tail[-1] == expected_newest),
                "retention_bounded": len(persisted) <= worker.max_log_lines,
            }
        )
        sample.require(
            bool(tail) and tail[-1] == expected_newest,
            "task-log tail must include the newest persisted row beyond 10k",
        )


def _session_events(start: int, stop: int) -> list[RuntimeEvent]:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    return [
        RuntimeEvent(
            type="session.created",
            aggregate_type="session",
            aggregate_id=f"session_{index:05d}",
            event_id=f"evt_session_{index:05d}",
            created_at=(base + timedelta(seconds=index)).isoformat(),
            payload={
                "core_id": "assistant",
                "core_revision": "rev_baseline",
                "status": "active",
                "target": {"core_revision": "rev_baseline", "metadata": {}},
                "created_at": (base + timedelta(seconds=index)).isoformat(),
                "updated_at": (base + timedelta(seconds=index)).isoformat(),
            },
        )
        for index in range(start, stop)
    ]


@pytest.mark.parametrize("session_count", [105, 10_005])
@pytest.mark.xfail(
    strict=True,
    raises=BaselineContractFailure,
    reason="STORE-01: bounded session queries truncate before selecting newest rows",
)
def test_store_01_latest_session_capacity_includes_newest(
    tmp_path,
    baseline_recorder,
    session_count,
):
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    runtime = SessionRuntime(control_plane=RuntimeControlPlane(store))

    with baseline_recorder.measure(
        "session_latest_query",
        finding="STORE-01",
        scale={"sessions": session_count, "requested": 20},
    ) as sample:
        for start in range(0, session_count, 5_000):
            store.append(_session_events(start, min(start + 5_000, session_count)))
        sessions = runtime.list_sessions(core_id="assistant", limit=20)
        newest = f"session_{session_count - 1:05d}"
        visible_ids = [session.session_id for session in sessions]
        sample.observations.update(
            {
                "returned": len(visible_ids),
                "newest_expected": newest,
                "newest_visible": newest in visible_ids,
                "first_visible": visible_ids[0] if visible_ids else None,
                "last_visible": visible_ids[-1] if visible_ids else None,
            }
        )
        sample.require(
            newest in visible_ids,
            "bounded latest-session query must include the newest session",
        )


@pytest.mark.xfail(
    strict=True,
    raises=BaselineContractFailure,
    reason="STORE-01: read_messages truncates the oldest 10k rows before selecting newest",
)
def test_store_01_latest_message_capacity_includes_newest(tmp_path, baseline_recorder):
    message_count = 10_005
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    runtime = SessionRuntime(control_plane=RuntimeControlPlane(store))
    runtime.create_session(session_id="session_messages", core_id="assistant", core_revision="rev_baseline")

    with baseline_recorder.measure(
        "session_message_latest_query",
        finding="STORE-01",
        scale={"messages": message_count, "query_cap": 10_000},
    ) as sample:
        for start in range(0, message_count, 5_000):
            stop = min(start + 5_000, message_count)
            store.append(
                [
                    RuntimeEvent(
                        type="message.persisted",
                        aggregate_type="message",
                        aggregate_id=f"message_{index:05d}",
                        event_id=f"evt_message_{index:05d}",
                        payload={
                            "session_id": "session_messages",
                            "turn_id": f"turn_{index:05d}",
                            "role": "user",
                            "visibility": "visible",
                            "content": {
                                "text": f"message-{index:05d}",
                                "kind": "message",
                                "model_visible": True,
                                "metadata": {},
                            },
                        },
                    )
                    for index in range(start, stop)
                ]
            )
        messages = runtime.read_messages("session_messages")
        expected_message_id = f"message_{message_count - 1:05d}"
        expected_turn_id = f"turn_{message_count - 1:05d}"
        sample.observations.update(
            {
                "returned": len(messages),
                "last_message_id": messages[-1].id if messages else None,
                "expected_message_id": expected_message_id,
                "latest_turn_id": runtime.latest_turn_id("session_messages"),
                "expected_turn_id": expected_turn_id,
            }
        )
        sample.require(
            messages[-1].id == expected_message_id
            and runtime.latest_turn_id("session_messages") == expected_turn_id,
            "10k+ message queries must include the newest message and turn",
        )
