from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from demiurge.runtime.durable_work import DurableClaimConflict, DurableWorkRuntime, DurableWorkSpec
from demiurge.runtime.store import RuntimeQuery, RuntimeStore


UTC = timezone.utc


def test_durable_work_claim_requires_current_claim_for_terminal_state(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    runtime = DurableWorkRuntime(store)
    now = datetime(2026, 7, 4, 1, 0, tzinfo=UTC)

    runtime.enqueue(
        DurableWorkSpec(
            work_id="delivery_1",
            kind="delivery.send",
            owner_session_id="session_1",
            owner_turn_id="turn_1",
            payload={"message_id": "msg_1"},
        ),
        now=now,
    )
    claim = runtime.claim_due(kind="delivery.send", owner_id="worker_a", now=now, lease_seconds=30)[0]
    runtime.mark_running(claim, now=now + timedelta(seconds=1))
    reclaimed = runtime.claim_due(
        kind="delivery.send",
        owner_id="worker_b",
        now=now + timedelta(seconds=31),
        lease_seconds=30,
    )[0]

    with pytest.raises(DurableClaimConflict):
        runtime.succeed(claim, external_ref="platform:old", now=now + timedelta(seconds=32))

    runtime.succeed(reclaimed, external_ref="platform:new", now=now + timedelta(seconds=33))
    row = store.query(RuntimeQuery(table="runtime_work_items", where={"work_id": "delivery_1"}, limit=1)).rows[0]

    assert row["status"] == "succeeded"
    assert row["claim_id"] == reclaimed.claim_id
    assert row["owner_id"] == "worker_b"
    assert row["external_ref"] == "platform:new"
    assert row["attempts"] == 2


def test_durable_work_recovery_marks_expired_sending_unknown_without_reclaim(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    runtime = DurableWorkRuntime(store)
    now = datetime(2026, 7, 4, 1, 0, tzinfo=UTC)

    runtime.enqueue(DurableWorkSpec(work_id="delivery_1", kind="delivery.send"), now=now)
    claim = runtime.claim_due(kind="delivery.send", owner_id="worker_a", now=now, lease_seconds=10)[0]
    runtime.mark_sending(claim, now=now + timedelta(seconds=1))

    summary = runtime.recover(now=now + timedelta(seconds=11))
    row = store.query(RuntimeQuery(table="runtime_work_items", where={"work_id": "delivery_1"}, limit=1)).rows[0]

    assert summary["unknown"] == 1
    assert row["status"] == "unknown"
    assert row["claim_id"] is None
    with pytest.raises(DurableClaimConflict):
        runtime.succeed(claim, external_ref="platform:late", now=now + timedelta(seconds=12))
    assert runtime.claim_due(kind="delivery.send", owner_id="worker_b", now=now + timedelta(seconds=12)) == []


def test_durable_work_recovery_requeues_expired_running_claims(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    runtime = DurableWorkRuntime(store)
    now = datetime(2026, 7, 4, 1, 0, tzinfo=UTC)

    runtime.enqueue(DurableWorkSpec(work_id="schedule_1", kind="schedule.fire"), now=now)
    claim = runtime.claim_due(kind="schedule.fire", owner_id="scheduler_a", now=now, lease_seconds=10)[0]
    runtime.mark_running(claim, now=now + timedelta(seconds=1))

    summary = runtime.recover(now=now + timedelta(seconds=11))
    row = store.query(RuntimeQuery(table="runtime_work_items", where={"work_id": "schedule_1"}, limit=1)).rows[0]
    reclaimed = runtime.claim_due(kind="schedule.fire", owner_id="scheduler_b", now=now + timedelta(seconds=12))[0]

    assert summary["recovered"] == 1
    assert row["status"] == "retry_scheduled"
    assert row["claim_id"] is None
    assert reclaimed.claim_id != claim.claim_id
    assert reclaimed.attempt == 2


def test_durable_work_retry_failure_is_not_terminal(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    runtime = DurableWorkRuntime(store)
    now = datetime(2026, 7, 4, 1, 0, tzinfo=UTC)
    retry_at = now + timedelta(minutes=5)

    runtime.enqueue(DurableWorkSpec(work_id="delivery_1", kind="delivery.send"), now=now)
    claim = runtime.claim_due(kind="delivery.send", owner_id="worker_a", now=now)[0]
    runtime.fail(claim, error="temporary", retry_at=retry_at, now=now + timedelta(seconds=1))
    row = store.query(RuntimeQuery(table="runtime_work_items", where={"work_id": "delivery_1"}, limit=1)).rows[0]

    assert row["status"] == "retry_scheduled"
    assert row["completed_at"] is None
    assert row["next_attempt_at"] == "2026-07-04T01:05:00Z"
    assert runtime.claim_due(kind="delivery.send", owner_id="worker_b", now=retry_at)[0].attempt == 2


def test_durable_work_recovery_quarantines_corrupt_payload_rows(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    now = "2026-07-04T01:00:00Z"
    store.transaction(
        lambda connection: connection.execute(
            """
            INSERT INTO runtime_work_items (
                work_id, kind, status, payload_json, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("work_corrupt", "delivery.send", "queued", "{bad json", now, now),
        )
    )

    summary = DurableWorkRuntime(store).recover(now=datetime(2026, 7, 4, 1, 1, tzinfo=UTC))
    row = store.query(RuntimeQuery(table="runtime_work_items", where={"work_id": "work_corrupt"}, limit=1)).rows[0]

    assert summary["corrupt"] == 1
    assert row["status"] == "corrupt"
