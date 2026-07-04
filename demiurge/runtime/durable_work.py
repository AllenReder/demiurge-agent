from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from demiurge.runtime.store import RuntimeEvent, RuntimeQuery, RuntimeStore
from demiurge.storage import utc_now
from demiurge.util import utc_id


UTC = timezone.utc
TERMINAL_STATUSES = {"succeeded", "failed", "cancelled", "acknowledged", "corrupt"}
NON_OVERWRITABLE_STATUSES = TERMINAL_STATUSES | {"unknown"}


class DurableClaimConflict(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class DurableWorkSpec:
    work_id: str
    kind: str
    owner_session_id: str | None = None
    owner_turn_id: str | None = None
    parent_work_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    next_attempt_at: str | None = None
    idempotency_key: str | None = None


@dataclass(frozen=True, slots=True)
class DurableWorkItem:
    work_id: str
    kind: str
    status: str
    owner_session_id: str | None = None
    owner_turn_id: str | None = None
    parent_work_id: str | None = None
    claim_id: str | None = None
    owner_id: str | None = None
    lease_expires_at: str | None = None
    attempts: int = 0
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DurableClaim:
    work_id: str
    kind: str
    claim_id: str
    owner_id: str
    lease_expires_at: str
    attempt: int


@dataclass(frozen=True, slots=True)
class DurableWorkOutcome:
    work_id: str
    status: str
    external_ref: str | None = None


class DurableWorkRuntime:
    """Host-owned durable work lifecycle manager backed by RuntimeStore."""

    def __init__(self, store: RuntimeStore):
        self.store = store

    def enqueue(self, spec: DurableWorkSpec, *, now: datetime | None = None) -> DurableWorkItem:
        self.store.append(
            [durable_work_enqueued_event(spec, created_at=now)],
            idempotency_key=spec.idempotency_key or f"work:{spec.work_id}:enqueued",
        )
        return self.get(spec.work_id)

    def get(self, work_id: str) -> DurableWorkItem:
        rows = self.store.query(RuntimeQuery(table="runtime_work_items", where={"work_id": work_id}, limit=1)).rows
        if not rows:
            raise KeyError(work_id)
        return _item_from_row(rows[0])

    def claim_due(
        self,
        *,
        kind: str | None = None,
        owner_id: str,
        now: datetime | None = None,
        lease_seconds: int = 60,
        limit: int = 1,
    ) -> list[DurableClaim]:
        now_text = _format(now)
        lease_expires_at = _format((_parse(now_text) + timedelta(seconds=lease_seconds)))

        def write(connection: sqlite3.Connection) -> list[DurableClaim]:
            clauses = [
                "status IN ('queued', 'retry_scheduled', 'claimed', 'running')",
                "(next_attempt_at IS NULL OR next_attempt_at <= ?)",
                "(status IN ('queued', 'retry_scheduled') OR lease_expires_at IS NULL OR lease_expires_at <= ?)",
            ]
            values: list[Any] = [now_text, now_text]
            if kind is not None:
                clauses.append("kind = ?")
                values.append(kind)
            rows = connection.execute(
                "SELECT * FROM runtime_work_items WHERE "
                + " AND ".join(clauses)
                + " ORDER BY created_at LIMIT ?",
                [*values, limit],
            ).fetchall()
            claims: list[DurableClaim] = []
            for raw in rows:
                row = dict(raw)
                claim_id = utc_id("claim_")
                attempts = int(row.get("attempts") or 0) + 1
                connection.execute(
                    """
                    UPDATE runtime_work_items
                    SET status = 'claimed',
                        claim_id = ?,
                        owner_id = ?,
                        claimed_at = ?,
                        lease_expires_at = ?,
                        attempts = ?,
                        updated_at = ?
                    WHERE work_id = ?
                    """,
                    (claim_id, owner_id, now_text, lease_expires_at, attempts, now_text, row["work_id"]),
                )
                claims.append(
                    DurableClaim(
                        work_id=str(row["work_id"]),
                        kind=str(row["kind"]),
                        claim_id=claim_id,
                        owner_id=owner_id,
                        lease_expires_at=lease_expires_at,
                        attempt=attempts,
                    )
                )
            return claims

        claims = self.store.transaction(write)
        for claim in claims:
            self._append_update(
                "work.claimed",
                claim.work_id,
                {
                    "status": "claimed",
                    "claim_id": claim.claim_id,
                    "owner_id": claim.owner_id,
                    "claimed_at": now_text,
                    "lease_expires_at": claim.lease_expires_at,
                    "attempts": claim.attempt,
                },
                now=now_text,
            )
        return claims

    def claim(
        self,
        work_id: str,
        *,
        owner_id: str,
        now: datetime | None = None,
        lease_seconds: int = 60,
    ) -> DurableClaim | None:
        now_text = _format(now)
        lease_expires_at = _format((_parse(now_text) + timedelta(seconds=lease_seconds)))

        def write(connection: sqlite3.Connection) -> DurableClaim | None:
            row = connection.execute(
                "SELECT * FROM runtime_work_items WHERE work_id = ?",
                (work_id,),
            ).fetchone()
            if row is None:
                return None
            current = dict(row)
            status = str(current.get("status") or "")
            lease = current.get("lease_expires_at")
            expired = lease is None or str(lease) <= now_text
            if status in TERMINAL_STATUSES or status == "unknown":
                return None
            if status not in {"queued", "retry_scheduled"} and not expired:
                return None
            claim_id = utc_id("claim_")
            attempts = int(current.get("attempts") or 0) + 1
            connection.execute(
                """
                UPDATE runtime_work_items
                SET status = 'claimed',
                    claim_id = ?,
                    owner_id = ?,
                    claimed_at = ?,
                    lease_expires_at = ?,
                    attempts = ?,
                    updated_at = ?
                WHERE work_id = ?
                """,
                (claim_id, owner_id, now_text, lease_expires_at, attempts, now_text, work_id),
            )
            return DurableClaim(
                work_id=work_id,
                kind=str(current["kind"]),
                claim_id=claim_id,
                owner_id=owner_id,
                lease_expires_at=lease_expires_at,
                attempt=attempts,
            )

        claim = self.store.transaction(write)
        if claim is None:
            return None
        self._append_update(
            "work.claimed",
            work_id,
            {
                "status": "claimed",
                "claim_id": claim.claim_id,
                "owner_id": claim.owner_id,
                "claimed_at": now_text,
                "lease_expires_at": claim.lease_expires_at,
                "attempts": claim.attempt,
            },
            now=now_text,
        )
        return claim

    def mark_running(self, claim: DurableClaim, *, now: datetime | None = None) -> DurableWorkItem:
        return self._transition(claim, "work.running", status="running", now=now)

    def mark_sending(self, claim: DurableClaim, *, now: datetime | None = None) -> DurableWorkItem:
        return self._transition(claim, "work.sending", status="sending", now=now)

    def heartbeat(self, claim: DurableClaim, *, now: datetime | None = None, lease_seconds: int = 60) -> DurableWorkItem:
        now_value = _parse(_format(now))
        lease_expires_at = _format(now_value + timedelta(seconds=lease_seconds))
        return self._transition(
            claim,
            "work.heartbeat",
            status=None,
            now=now,
            extra={"lease_expires_at": lease_expires_at},
        )

    def succeed(
        self,
        claim: DurableClaim,
        *,
        external_ref: str | None = None,
        now: datetime | None = None,
    ) -> DurableWorkOutcome:
        item = self._transition(
            claim,
            "work.succeeded",
            status="succeeded",
            now=now,
            extra={"external_ref": external_ref},
            terminal=True,
        )
        return DurableWorkOutcome(work_id=item.work_id, status=item.status, external_ref=external_ref)

    def fail(
        self,
        claim: DurableClaim,
        *,
        error: str,
        retry_at: datetime | None = None,
        now: datetime | None = None,
    ) -> DurableWorkOutcome:
        status = "retry_scheduled" if retry_at is not None else "failed"
        item = self._transition(
            claim,
            "work.failed",
            status=status,
            now=now,
            extra={"last_error": error, "next_attempt_at": _format(retry_at) if retry_at is not None else None},
            terminal=retry_at is None,
        )
        return DurableWorkOutcome(work_id=item.work_id, status=item.status)

    def cancel(self, claim: DurableClaim, *, reason: str = "cancelled", now: datetime | None = None) -> DurableWorkOutcome:
        item = self._transition(
            claim,
            "work.cancelled",
            status="cancelled",
            now=now,
            extra={"last_error": reason},
            terminal=True,
        )
        return DurableWorkOutcome(work_id=item.work_id, status=item.status)

    def mark_unknown(
        self,
        claim: DurableClaim,
        *,
        reason: str,
        now: datetime | None = None,
    ) -> DurableWorkOutcome:
        item = self._transition(
            claim,
            "work.unknown",
            status="unknown",
            now=now,
            extra={"last_error": reason},
        )
        return DurableWorkOutcome(work_id=item.work_id, status=item.status)

    def acknowledge(self, claim: DurableClaim, *, now: datetime | None = None) -> DurableWorkOutcome:
        item = self._transition(claim, "work.acknowledged", status="acknowledged", now=now, terminal=True)
        return DurableWorkOutcome(work_id=item.work_id, status=item.status)

    def recover(self, *, now: datetime | None = None) -> dict[str, int]:
        now_text = _format(now)
        summary = {"recovered": 0, "deferred": 0, "unknown": 0, "corrupt": 0}

        def write(connection: sqlite3.Connection) -> dict[str, int]:
            rows = connection.execute("SELECT * FROM runtime_work_items").fetchall()
            for raw in rows:
                row = dict(raw)
                try:
                    json.loads(str(row.get("payload_json") or "{}"))
                except json.JSONDecodeError:
                    connection.execute(
                        """
                        UPDATE runtime_work_items
                        SET status = 'corrupt', last_error = ?, updated_at = ?, completed_at = COALESCE(completed_at, ?)
                        WHERE work_id = ?
                        """,
                        ("corrupt payload_json", now_text, now_text, row["work_id"]),
                    )
                    summary["corrupt"] += 1
                    continue
                status = str(row.get("status") or "")
                lease = row.get("lease_expires_at")
                expired = lease is not None and str(lease) <= now_text
                if status == "sending" and expired:
                    connection.execute(
                        """
                        UPDATE runtime_work_items
                        SET status = 'unknown',
                            claim_id = NULL,
                            owner_id = NULL,
                            claimed_at = NULL,
                            lease_expires_at = NULL,
                            last_error = ?,
                            updated_at = ?
                        WHERE work_id = ?
                        """,
                        ("expired while sending", now_text, row["work_id"]),
                    )
                    summary["unknown"] += 1
                elif status == "unknown":
                    summary["unknown"] += 1
                elif status in {"claimed", "running"} and expired:
                    connection.execute(
                        """
                        UPDATE runtime_work_items
                        SET status = 'retry_scheduled',
                            claim_id = NULL,
                            owner_id = NULL,
                            claimed_at = NULL,
                            lease_expires_at = NULL,
                            updated_at = ?,
                            last_error = COALESCE(last_error, ?)
                        WHERE work_id = ?
                        """,
                        (now_text, "expired claim recovered", row["work_id"]),
                    )
                    summary["recovered"] += 1
                elif status not in TERMINAL_STATUSES:
                    summary["deferred"] += 1
            return summary

        return self.store.transaction(write)

    def _transition(
        self,
        claim: DurableClaim,
        event_type: str,
        *,
        status: str | None,
        now: datetime | None,
        extra: dict[str, Any] | None = None,
        terminal: bool = False,
    ) -> DurableWorkItem:
        now_text = _format(now)
        values = dict(extra or {})
        if status is not None:
            values["status"] = status
        values["claim_id"] = claim.claim_id
        values["owner_id"] = claim.owner_id

        def write(connection: sqlite3.Connection) -> None:
            row = connection.execute(
                "SELECT claim_id, status FROM runtime_work_items WHERE work_id = ?",
                (claim.work_id,),
            ).fetchone()
            if row is None:
                raise KeyError(claim.work_id)
            if str(row["claim_id"]) != claim.claim_id:
                raise DurableClaimConflict(f"stale durable claim for work {claim.work_id}")
            if str(row["status"]) in NON_OVERWRITABLE_STATUSES:
                raise DurableClaimConflict(f"durable work cannot be overwritten: {claim.work_id}")
            completed_at = now_text if terminal else None
            connection.execute(
                """
                UPDATE runtime_work_items
                SET status = COALESCE(?, status),
                    owner_id = COALESCE(?, owner_id),
                    lease_expires_at = COALESCE(?, lease_expires_at),
                    next_attempt_at = ?,
                    last_error = COALESCE(?, last_error),
                    external_ref = COALESCE(?, external_ref),
                    updated_at = ?,
                    completed_at = COALESCE(?, completed_at)
                WHERE work_id = ?
                """,
                (
                    status,
                    claim.owner_id,
                    values.get("lease_expires_at"),
                    values.get("next_attempt_at"),
                    values.get("last_error"),
                    values.get("external_ref"),
                    now_text,
                    completed_at,
                    claim.work_id,
                ),
            )

        self.store.transaction(write)
        self._append_update(event_type, claim.work_id, values, now=now_text)
        return self.get(claim.work_id)

    def _append_update(self, event_type: str, work_id: str, payload: dict[str, Any], *, now: str) -> None:
        self.store.append(
            [
                RuntimeEvent(
                    type=event_type,
                    aggregate_type="work",
                    aggregate_id=work_id,
                    payload=payload,
                    created_at=now,
                )
            ]
        )


def durable_work_enqueued_event(
    spec: DurableWorkSpec,
    *,
    created_at: datetime | str | None = None,
    actor: str | None = None,
) -> RuntimeEvent:
    created_at_text = _format(created_at)
    return RuntimeEvent(
        type="work.enqueued",
        aggregate_type="work",
        aggregate_id=spec.work_id,
        actor=actor,
        payload={
            "kind": spec.kind,
            "status": "queued",
            "owner_session_id": spec.owner_session_id,
            "owner_turn_id": spec.owner_turn_id,
            "parent_work_id": spec.parent_work_id,
            "payload": dict(spec.payload),
            "next_attempt_at": spec.next_attempt_at,
            "created_at": created_at_text,
        },
        created_at=created_at_text,
    )


def _item_from_row(row: dict[str, Any]) -> DurableWorkItem:
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    return DurableWorkItem(
        work_id=str(row["work_id"]),
        kind=str(row.get("kind") or ""),
        status=str(row.get("status") or ""),
        owner_session_id=row.get("owner_session_id"),
        owner_turn_id=row.get("owner_turn_id"),
        parent_work_id=row.get("parent_work_id"),
        claim_id=row.get("claim_id"),
        owner_id=row.get("owner_id"),
        lease_expires_at=row.get("lease_expires_at"),
        attempts=int(row.get("attempts") or 0),
        payload=dict(payload),
    )


def _format(value: datetime | str | None) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return utc_now()
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse(value: str) -> datetime:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
