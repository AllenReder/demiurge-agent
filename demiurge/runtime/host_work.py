from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping

from demiurge.runtime.durable_work import DurableClaim, DurableWorkItem, DurableWorkOutcome, DurableWorkRuntime, DurableWorkSpec
from demiurge.runtime.store import RuntimeEvent, RuntimeQuery, RuntimeStore


HOST_TASK_KINDS = frozenset({"agent.spawn", "terminal.exec", "evolver.run", "schedule.fire"})
HOST_EVENT_AGGREGATES = frozenset({"work", "task", "task_completion", "delivery", "scheduler_instance"})


@dataclass(frozen=True, slots=True)
class HostWorkEvent:
    seq: int
    event_id: str
    type: str
    aggregate_type: str
    aggregate_id: str
    created_at: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    actor: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class HostWorkItem:
    work_id: str
    kind: str
    status: str
    source: str
    work_status: str | None = None
    task_status: str | None = None
    owner_session_id: str | None = None
    owner_turn_id: str | None = None
    parent_work_id: str | None = None
    task_id: str | None = None
    delivery_id: str | None = None
    schedule_id: str | None = None
    run_id: str | None = None
    claim_id: str | None = None
    owner_id: str | None = None
    attempts: int = 0
    lease_expires_at: str | None = None
    next_attempt_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    completed_at: str | None = None
    summary: str = ""
    result_ref: str | None = None
    last_error: str | None = None
    external_ref: str | None = None
    log_tail: tuple[str, ...] = ()
    payload: Mapping[str, Any] = field(default_factory=dict)
    details: Mapping[str, Any] = field(default_factory=dict)

    @property
    def running(self) -> bool:
        return self.status in {"queued", "claimed", "running", "sending", "retry_scheduled", "blocked_needs_user"}


class HostWorkLifecycleRuntime:
    """Unified host-owned lifecycle and observation facade for detached work."""

    def __init__(
        self,
        *,
        store: RuntimeStore,
        durable_work: DurableWorkRuntime | None = None,
    ):
        self.store = store
        self.durable_work = durable_work or DurableWorkRuntime(store)

    def enqueue(self, spec: DurableWorkSpec, *, now: datetime | None = None) -> DurableWorkItem:
        return self.durable_work.enqueue(spec, now=now)

    def ensure(self, spec: DurableWorkSpec, *, now: datetime | None = None) -> DurableWorkItem:
        rows = self.store.query(RuntimeQuery(table="runtime_work_items", where={"work_id": spec.work_id}, limit=1)).rows
        if rows:
            return self.durable_work.get(spec.work_id)
        return self.enqueue(spec, now=now)

    def claim_due(
        self,
        *,
        kind: str | None = None,
        owner_id: str,
        now: datetime | None = None,
        lease_seconds: int = 60,
        limit: int = 1,
    ) -> list[DurableClaim]:
        return self.durable_work.claim_due(
            kind=kind,
            owner_id=owner_id,
            now=now,
            lease_seconds=lease_seconds,
            limit=limit,
        )

    def claim(
        self,
        work_id: str,
        *,
        owner_id: str,
        now: datetime | None = None,
        lease_seconds: int = 60,
    ) -> DurableClaim | None:
        return self.durable_work.claim(work_id, owner_id=owner_id, now=now, lease_seconds=lease_seconds)

    def running(self, claim: DurableClaim, *, now: datetime | None = None) -> DurableWorkItem:
        return self.durable_work.mark_running(claim, now=now)

    def sending(self, claim: DurableClaim, *, now: datetime | None = None) -> DurableWorkItem:
        return self.durable_work.mark_sending(claim, now=now)

    def complete(
        self,
        claim: DurableClaim,
        *,
        external_ref: str | None = None,
        now: datetime | None = None,
    ) -> DurableWorkOutcome:
        return self.durable_work.succeed(claim, external_ref=external_ref, now=now)

    def fail(
        self,
        claim: DurableClaim,
        *,
        error: str,
        retry_at: datetime | None = None,
        now: datetime | None = None,
    ) -> DurableWorkOutcome:
        return self.durable_work.fail(claim, error=error, retry_at=retry_at, now=now)

    def cancel(
        self,
        claim: DurableClaim,
        *,
        reason: str = "cancelled",
        now: datetime | None = None,
    ) -> DurableWorkOutcome:
        return self.durable_work.cancel(claim, reason=reason, now=now)

    def mark_unknown(
        self,
        claim: DurableClaim,
        *,
        reason: str,
        now: datetime | None = None,
    ) -> DurableWorkOutcome:
        return self.durable_work.mark_unknown(claim, reason=reason, now=now)

    def acknowledge(self, claim: DurableClaim, *, now: datetime | None = None) -> DurableWorkOutcome:
        outcome = self.durable_work.acknowledge(claim, now=now)
        if claim.kind == "task.completion":
            self._acknowledge_task_completion(claim.work_id)
        return outcome

    def acknowledge_by_id(self, work_id: str, *, claim_id: str) -> bool:
        rows = self.store.query(RuntimeQuery(table="runtime_work_items", where={"work_id": work_id}, limit=1)).rows
        if not rows:
            return False
        row = rows[0]
        if str(row.get("claim_id") or "") != claim_id:
            return False
        claim = DurableClaim(
            work_id=work_id,
            kind=str(row.get("kind") or ""),
            claim_id=claim_id,
            owner_id=str(row.get("owner_id") or "host.work_lifecycle"),
            lease_expires_at=str(row.get("lease_expires_at") or ""),
            attempt=int(row.get("attempts") or 0),
        )
        try:
            self.acknowledge(claim)
        except Exception:
            return False
        return True

    def recover(self, *, now: datetime | None = None) -> dict[str, int]:
        return self.durable_work.recover(now=now)

    def status(self, work_id: str) -> HostWorkItem:
        row = self._first("runtime_work_items", work_id=work_id)
        if row is not None:
            return self._item_from_work(row)
        row = self._first("tasks", task_id=work_id)
        if row is not None and str(row.get("kind") or "") in HOST_TASK_KINDS:
            return self._item_from_task(row)
        row = self._first("outbox", delivery_id=work_id)
        if row is not None:
            return self._item_from_delivery(row)
        raise KeyError(work_id)

    def list_work(
        self,
        *,
        kind: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[HostWorkItem]:
        items: list[HostWorkItem] = []
        work_where: dict[str, Any] = {}
        if kind is not None:
            work_where["kind"] = kind
        for row in self.store.query(
            RuntimeQuery(table="runtime_work_items", where=work_where, order_by="created_at", limit=limit)
        ).rows:
            item = self._item_from_work(row)
            if status is None or item.status == status:
                items.append(item)
        if kind in {None, *HOST_TASK_KINDS}:
            task_where: dict[str, Any] = {}
            if kind in HOST_TASK_KINDS:
                task_where["kind"] = kind
            for row in self.store.query(RuntimeQuery(table="tasks", where=task_where, order_by="created_at", limit=limit)).rows:
                if str(row.get("kind") or "") not in HOST_TASK_KINDS:
                    continue
                item = self._item_from_task(row)
                if status is None or item.status == status:
                    items.append(item)
        return self._dedupe_items(items)[:limit]

    def list_session_work(
        self,
        session_id: str,
        *,
        kind: str | None = None,
        include_completed: bool = True,
        limit: int = 100,
    ) -> list[HostWorkItem]:
        items: list[HostWorkItem] = []
        work_where: dict[str, Any] = {"owner_session_id": session_id}
        if kind is not None:
            work_where["kind"] = kind
        for row in self.store.query(
            RuntimeQuery(table="runtime_work_items", where=work_where, order_by="created_at", limit=limit)
        ).rows:
            items.append(self._item_from_work(row))
        task_where: dict[str, Any] = {"owner_session_id": session_id}
        if kind in HOST_TASK_KINDS:
            task_where["kind"] = kind
        if kind is None or kind in HOST_TASK_KINDS:
            for row in self.store.query(RuntimeQuery(table="tasks", where=task_where, order_by="created_at", limit=limit)).rows:
                if str(row.get("kind") or "") not in HOST_TASK_KINDS:
                    continue
                items.append(self._item_from_task(row))
        items = self._dedupe_items(items)
        if not include_completed:
            items = [item for item in items if item.running]
        return items[:limit]

    def list_events(
        self,
        *,
        work_id: str | None = None,
        task_id: str | None = None,
        session_id: str | None = None,
        kind: str | None = None,
        limit: int = 100,
    ) -> list[HostWorkEvent]:
        rows: list[dict[str, Any]] = []
        if work_id is not None:
            rows.extend(self._event_rows_for_item(self.status(work_id)))
        elif task_id is not None:
            rows.extend(self._task_event_rows(task_id))
            rows.extend(self._completion_event_rows(task_id=task_id))
            for work in self.store.query(
                RuntimeQuery(table="runtime_work_items", where={"parent_work_id": task_id}, order_by="created_at", limit=1000)
            ).rows:
                rows.extend(self._event_rows_for_item(self._item_from_work(work)))
        elif session_id is not None:
            for item in self.list_session_work(session_id, kind=kind, limit=1000):
                rows.extend(self._event_rows_for_item(item))
        elif kind is not None:
            for item in self.list_work(kind=kind, limit=1000):
                rows.extend(self._event_rows_for_item(item))
        else:
            rows = [
                row
                for row in self.store.query(RuntimeQuery(table="runtime_events", order_by="seq", limit=limit)).rows
                if row.get("aggregate_type") in HOST_EVENT_AGGREGATES
            ]
        return [self._event_from_row(row) for row in self._dedupe_event_rows(rows)[:limit]]

    def _item_from_work(self, row: Mapping[str, Any]) -> HostWorkItem:
        work_id = str(row["work_id"])
        kind = str(row.get("kind") or "")
        payload = row.get("payload") if isinstance(row.get("payload"), Mapping) else {}
        details: dict[str, Any] = {"work": dict(row)}
        task_id = str(payload.get("task_id") or row.get("parent_work_id") or "") or None
        delivery_id: str | None = work_id if kind == "delivery.send" else None
        schedule_id = str(payload.get("schedule_id") or "") or None
        run_id: str | None = None
        task_status: str | None = None
        summary = ""
        result_ref = row.get("external_ref")
        log_tail: tuple[str, ...] = ()
        status = str(row.get("status") or "")

        if kind == "delivery.send":
            delivery = self._first("outbox", delivery_id=work_id)
            if delivery is not None:
                details["delivery"] = delivery
                status = self._effective_delivery_status(work_status=status, delivery_status=str(delivery.get("status") or ""))
        elif kind == "task.completion":
            task_id = str(payload.get("task_id") or row.get("parent_work_id") or "") or None
            task = self._first("tasks", task_id=task_id) if task_id else None
            if task is not None and str(task.get("kind") or "") in HOST_TASK_KINDS:
                task_status = str(task.get("status") or "")
                task_summary, task_metadata, event_result_ref = self._task_event_payload(task_id or "")
                summary = str(payload.get("summary") or task_summary)
                result_ref = payload.get("result_ref") or event_result_ref or result_ref
                details["task"] = {**task, "metadata": task_metadata}
            log_tail = tuple(str(line) for line in payload.get("log_tail") or ())
        elif kind == "schedule.fire":
            scheduler = self._schedule_row_for_work(payload)
            if scheduler is not None:
                details["scheduler"] = scheduler
                status = str(scheduler.get("claim_status") or status)
                run_id = str(scheduler.get("task_id") or "") or None
            task_id = run_id or task_id
            if task_id:
                task = self._first("tasks", task_id=task_id)
                if task is not None and str(task.get("kind") or "") in HOST_TASK_KINDS:
                    task_status = str(task.get("status") or "")
                    summary, task_metadata, event_result_ref = self._task_event_payload(task_id)
                    result_ref = task.get("result_ref") or event_result_ref or result_ref
                    details["task"] = {**task, "metadata": task_metadata}

        return HostWorkItem(
            work_id=work_id,
            kind=kind,
            status=status,
            source="durable_work",
            work_status=str(row.get("status") or ""),
            task_status=task_status,
            owner_session_id=row.get("owner_session_id"),
            owner_turn_id=row.get("owner_turn_id"),
            parent_work_id=row.get("parent_work_id"),
            task_id=task_id,
            delivery_id=delivery_id,
            schedule_id=schedule_id,
            run_id=run_id,
            claim_id=row.get("claim_id"),
            owner_id=row.get("owner_id"),
            attempts=int(row.get("attempts") or 0),
            lease_expires_at=row.get("lease_expires_at"),
            next_attempt_at=row.get("next_attempt_at"),
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
            completed_at=row.get("completed_at"),
            summary=summary,
            result_ref=result_ref,
            last_error=row.get("last_error"),
            external_ref=row.get("external_ref"),
            log_tail=log_tail,
            payload=dict(payload),
            details=details,
        )

    def _item_from_task(self, row: Mapping[str, Any]) -> HostWorkItem:
        task_id = str(row["task_id"])
        kind = str(row.get("kind") or "")
        summary, metadata, result_ref = self._task_event_payload(task_id)
        source = row.get("source") if isinstance(row.get("source"), Mapping) else {}
        action = self._task_submitted_action(task_id)
        source_tool = str(action.get("source_tool") or (source.get("metadata") or {}).get("source_tool") or "")
        details: dict[str, Any] = {"task": {**dict(row), "metadata": metadata, "source_tool": source_tool}}
        schedule = self._first("scheduler_instances", task_id=task_id) if kind == "schedule.fire" else None
        work_id = task_id
        schedule_id: str | None = None
        run_id: str | None = task_id if kind == "schedule.fire" else None
        if schedule is not None:
            details["scheduler"] = schedule
            schedule_id = str(schedule.get("schedule_id") or "") or None
            core_id = str(schedule.get("core_id") or "")
            due_at = str(schedule.get("due_at") or "")
            if core_id and schedule_id and due_at:
                work_id = f"schedule:{core_id}:{schedule_id}:{due_at}"
        return HostWorkItem(
            work_id=work_id,
            kind=kind,
            status=str(row.get("status") or "queued"),
            source="task",
            task_status=str(row.get("status") or "queued"),
            owner_session_id=row.get("owner_session_id"),
            owner_turn_id=row.get("owner_turn_id"),
            task_id=task_id,
            schedule_id=schedule_id,
            run_id=run_id,
            created_at=row.get("created_at"),
            updated_at=row.get("heartbeat_at") or row.get("completed_at") or row.get("started_at") or row.get("created_at"),
            completed_at=row.get("completed_at"),
            summary=summary,
            result_ref=row.get("result_ref") or result_ref,
            last_error=(row.get("error") or {}).get("message") if isinstance(row.get("error"), Mapping) else None,
            log_tail=tuple(self._task_logs(task_id)[-40:]),
            payload=dict(action),
            details=details,
        )

    def _item_from_delivery(self, row: Mapping[str, Any]) -> HostWorkItem:
        delivery_id = str(row["delivery_id"])
        payload = row.get("payload") if isinstance(row.get("payload"), Mapping) else {}
        return HostWorkItem(
            work_id=delivery_id,
            kind="delivery.send",
            status=str(row.get("status") or "queued"),
            source="delivery",
            owner_turn_id=row.get("owner_turn_id"),
            delivery_id=delivery_id,
            attempts=int(row.get("attempts") or 0),
            created_at=row.get("created_at"),
            updated_at=row.get("sent_at") or row.get("created_at"),
            completed_at=row.get("sent_at"),
            last_error=row.get("last_error"),
            payload=dict(payload),
            details={"delivery": dict(row)},
        )

    def _event_rows_for_item(self, item: HostWorkItem) -> list[dict[str, Any]]:
        rows = self._event_rows(aggregate_type="work", aggregate_id=item.work_id)
        if item.delivery_id is not None:
            rows.extend(self._event_rows(aggregate_type="delivery", aggregate_id=item.delivery_id))
        if item.task_id is not None:
            rows.extend(self._task_event_rows(item.task_id))
            rows.extend(self._completion_event_rows(task_id=item.task_id))
        if item.kind == "task.completion":
            rows.extend(self._event_rows(aggregate_type="task_completion", aggregate_id=item.work_id))
        if item.schedule_id is not None or item.run_id is not None:
            rows.extend(self._scheduler_event_rows(item))
        return rows

    def _task_event_rows(self, task_id: str) -> list[dict[str, Any]]:
        return self._event_rows(aggregate_type="task", aggregate_id=task_id)

    def _completion_event_rows(self, *, task_id: str) -> list[dict[str, Any]]:
        return [
            row
            for row in self.store.query(
                RuntimeQuery(table="runtime_events", where={"aggregate_type": "task_completion"}, order_by="seq", limit=10_000)
            ).rows
            if (row.get("payload") or {}).get("task_id") == task_id
        ]

    def _scheduler_event_rows(self, item: HostWorkItem) -> list[dict[str, Any]]:
        events = self.store.query(
            RuntimeQuery(table="runtime_events", where={"aggregate_type": "scheduler_instance"}, order_by="seq", limit=10_000)
        ).rows
        due_at = (item.payload or {}).get("due_at") or (item.details.get("scheduler") or {}).get("due_at")
        return [
            row
            for row in events
            if (
                (item.run_id and (row.get("payload") or {}).get("task_id") == item.run_id)
                or (
                    item.schedule_id
                    and due_at
                    and (row.get("payload") or {}).get("schedule_id") == item.schedule_id
                    and (row.get("payload") or {}).get("due_at") == due_at
                )
            )
        ]

    def _event_rows(self, *, aggregate_type: str, aggregate_id: str) -> list[dict[str, Any]]:
        return list(
            self.store.query(
                RuntimeQuery(
                    table="runtime_events",
                    where={"aggregate_type": aggregate_type, "aggregate_id": aggregate_id},
                    order_by="seq",
                    limit=10_000,
                )
            ).rows
        )

    def _task_event_payload(self, task_id: str) -> tuple[str, dict[str, Any], str | None]:
        summary = ""
        metadata: dict[str, Any] = {}
        result_ref: str | None = None
        for event in self._task_event_rows(task_id):
            payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
            if isinstance(payload.get("metadata"), Mapping):
                metadata.update(dict(payload["metadata"]))
            if payload.get("summary") is not None:
                summary = str(payload.get("summary") or "")
            error = payload.get("error") if isinstance(payload.get("error"), Mapping) else {}
            if not summary and error.get("message"):
                summary = str(error["message"])
            if payload.get("result_ref") is not None:
                result_ref = str(payload["result_ref"])
        return summary, metadata, result_ref

    def _task_submitted_action(self, task_id: str) -> dict[str, Any]:
        for event in self._task_event_rows(task_id):
            if event.get("type") != "task.submitted":
                continue
            payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
            action = payload.get("action") if isinstance(payload.get("action"), Mapping) else {}
            return dict(action)
        return {}

    def _task_logs(self, task_id: str) -> list[str]:
        return [
            str(row.get("text") or "")
            for row in self.store.query(RuntimeQuery(table="task_logs", where={"task_id": task_id}, order_by="seq", limit=10_000)).rows
        ]

    def _schedule_row_for_work(self, payload: Mapping[str, Any]) -> dict[str, Any] | None:
        core_id = payload.get("core_id")
        schedule_id = payload.get("schedule_id")
        due_at = payload.get("due_at")
        if not core_id or not schedule_id or not due_at:
            return None
        return self._first("scheduler_instances", core_id=str(core_id), schedule_id=str(schedule_id), due_at=str(due_at))

    def _effective_delivery_status(self, *, work_status: str, delivery_status: str) -> str:
        if work_status in {"claimed", "sending", "retry_scheduled", "unknown"}:
            return work_status
        if delivery_status:
            return delivery_status
        return work_status

    def _acknowledge_task_completion(self, event_id: str) -> None:
        self.store.append(
            [
                RuntimeEvent(
                    type="task.completion_acknowledged",
                    aggregate_type="task_completion",
                    aggregate_id=event_id,
                    payload={"event_id": event_id, "status": "acknowledged"},
                )
            ],
            idempotency_key=f"task_completion:{event_id}:acknowledged",
        )

    def _first(self, table: Any, **where: Any) -> dict[str, Any] | None:
        rows = self.store.query(RuntimeQuery(table=table, where=dict(where), limit=1)).rows
        return dict(rows[0]) if rows else None

    def _dedupe_items(self, items: list[HostWorkItem]) -> list[HostWorkItem]:
        deduped: dict[tuple[str, str], HostWorkItem] = {}
        for item in items:
            key = (item.source, item.work_id)
            if item.source == "task" and item.task_id:
                key = ("task", item.task_id)
            deduped[key] = item
        return sorted(
            deduped.values(),
            key=lambda item: item.updated_at or item.created_at or "",
            reverse=True,
        )

    def _dedupe_event_rows(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        deduped: dict[int, dict[str, Any]] = {}
        for row in rows:
            deduped[int(row["seq"])] = row
        return [deduped[seq] for seq in sorted(deduped)]

    def _event_from_row(self, row: Mapping[str, Any]) -> HostWorkEvent:
        return HostWorkEvent(
            seq=int(row["seq"]),
            event_id=str(row["event_id"]),
            type=str(row["type"]),
            aggregate_type=str(row["aggregate_type"]),
            aggregate_id=str(row["aggregate_id"]),
            created_at=str(row["created_at"]),
            payload=row.get("payload") if isinstance(row.get("payload"), Mapping) else {},
            actor=row.get("actor") if isinstance(row.get("actor"), Mapping) else {},
        )
