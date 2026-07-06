from __future__ import annotations

import asyncio
import contextvars
import inspect
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable, Literal, Mapping

from demiurge.runtime.control import RuntimeControlPlane, TaskSource, TaskSpec
from demiurge.runtime.durable_work import DurableClaim, DurableWorkRuntime
from demiurge.runtime.store import RuntimeQuery
from demiurge.util import utc_id


RuntimeTaskStatus = Literal["queued", "running", "blocked_needs_user", "succeeded", "failed", "cancelled", "lost"]
RuntimeTaskKind = Literal["terminal.exec", "evolver.run", "agent.spawn"]
TERMINAL_TASK_STATUSES = {"succeeded", "failed", "cancelled", "lost"}
BACKGROUND_TASK_KINDS = frozenset({"terminal.exec", "evolver.run", "agent.spawn"})

RuntimeTaskCancelCallback = Callable[[], Any | Awaitable[Any]]
RuntimeTaskCompletionCallback = Callable[["RuntimeTaskCompletionEvent"], Any | Awaitable[Any]]
RuntimeTaskFactory = Callable[["RuntimeTaskContext"], Awaitable[Any]]


class RuntimeTaskConflictError(RuntimeError):
    pass


class RuntimeTaskKindError(ValueError):
    pass


@dataclass(slots=True)
class RuntimeTaskOutcome:
    summary: str = ""
    result_ref: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RuntimeTaskRecord:
    task_id: str
    kind: RuntimeTaskKind
    owner_session_id: str
    owner_turn_id: str
    source_tool: str
    write_scope: str | None
    status: RuntimeTaskStatus
    started_at: str | None = None
    completed_at: str | None = None
    summary: str = ""
    log_tail: list[str] = field(default_factory=list)
    result_ref: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    notify_on_complete: bool = True
    task: asyncio.Task[Any] | None = field(default=None, repr=False, compare=False)

    @property
    def running(self) -> bool:
        return self.status in {"queued", "running", "blocked_needs_user"}

    def to_payload(self, *, include_log: bool = False, log: list[str] | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "task_id": self.task_id,
            "kind": self.kind,
            "owner_session_id": self.owner_session_id,
            "owner_turn_id": self.owner_turn_id,
            "source_tool": self.source_tool,
            "write_scope": self.write_scope,
            "status": self.status,
            "running": self.running,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "summary": self.summary,
            "log_tail": list(self.log_tail),
            "result_ref": self.result_ref,
            "metadata": dict(self.metadata),
            "notify_on_complete": self.notify_on_complete,
        }
        if include_log:
            payload["log"] = list(log or [])
        return payload


@dataclass(frozen=True, slots=True)
class RuntimeTaskCompletionEvent:
    event_id: str
    task_id: str
    kind: str
    owner_session_id: str
    owner_turn_id: str
    source_tool: str
    status: RuntimeTaskStatus
    summary: str
    log_tail: tuple[str, ...] = ()
    result_ref: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_inbound_text(self) -> str:
        lines = [
            "[SYSTEM: Background task event]",
            f"task_id: {self.task_id}",
            f"kind: {self.kind}",
            f"source_tool: {self.source_tool}",
            f"status: {self.status}",
        ]
        if self.summary:
            lines.extend(["", "Summary:", self.summary])
        if self.result_ref:
            lines.extend(["", f"Result ref: {self.result_ref}"])
        if self.log_tail:
            lines.extend(["", "Log tail:"])
            lines.extend(self.log_tail)
        lines.extend(
            [
                "",
                "Respond to the user with a concise status update. Do not rerun the task. "
                "If more detail is needed, use task_status or task_list to inspect it.",
            ]
        )
        return "\n".join(lines).strip()

    def to_metadata(self) -> dict[str, Any]:
        return {
            "synthetic": True,
            "trigger": "background_task",
            "event_id": self.event_id,
            "task_id": self.task_id,
            "task_kind": self.kind,
            "source_tool": self.source_tool,
            "task_status": self.status,
            "owner_turn_id": self.owner_turn_id,
            "result_ref": self.result_ref,
        }

    def to_payload(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "kind": self.kind,
            "owner_session_id": self.owner_session_id,
            "owner_turn_id": self.owner_turn_id,
            "source_tool": self.source_tool,
            "status": self.status,
            "summary": self.summary,
            "log_tail": list(self.log_tail),
            "result_ref": self.result_ref,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_runtime_event(cls, event: Mapping[str, Any]) -> "RuntimeTaskCompletionEvent":
        payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
        return cls(
            event_id=str(event.get("aggregate_id") or event.get("event_id") or ""),
            task_id=str(payload.get("task_id") or ""),
            kind=str(payload.get("kind") or ""),
            owner_session_id=str(payload.get("owner_session_id") or ""),
            owner_turn_id=str(payload.get("owner_turn_id") or ""),
            source_tool=str(payload.get("source_tool") or ""),
            status=str(payload.get("status") or "failed"),  # type: ignore[arg-type]
            summary=str(payload.get("summary") or ""),
            log_tail=tuple(str(line) for line in payload.get("log_tail") or ()),
            result_ref=payload.get("result_ref"),
            metadata=payload.get("metadata") if isinstance(payload.get("metadata"), Mapping) else {},
        )


class RuntimeTaskContext:
    def __init__(self, runtime: "RuntimeTaskWorker", task_id: str):
        self.runtime = runtime
        self.task_id = task_id

    def append_log(self, text: str) -> None:
        self.runtime.append_log(self.task_id, text)

    def set_summary(self, summary: str) -> None:
        self.runtime.set_summary(self.task_id, summary)

    def set_result_ref(self, result_ref: str | None) -> None:
        self.runtime.set_result_ref(self.task_id, result_ref)

    def update_metadata(self, values: Mapping[str, Any]) -> None:
        self.runtime.update_metadata(self.task_id, values)

    def set_cancel_callback(self, callback: RuntimeTaskCancelCallback) -> None:
        self.runtime.set_cancel_callback(self.task_id, callback)

    def mark_blocked(self, summary: str, *, metadata: Mapping[str, Any] | None = None) -> None:
        self.runtime.mark_blocked(self.task_id, summary, metadata=metadata)


class RuntimeTaskWorker:
    """Live worker for active background RuntimeControlPlane tasks."""

    def __init__(
        self,
        *,
        max_log_lines: int = 4000,
        log_tail_lines: int = 40,
        log_tail_chars: int = 8000,
        control_plane: RuntimeControlPlane,
    ):
        self.max_log_lines = max_log_lines
        self.log_tail_lines = log_tail_lines
        self.log_tail_chars = log_tail_chars
        self.control_plane = control_plane
        self._active_records: dict[str, RuntimeTaskRecord] = {}
        self._active_tasks: dict[str, asyncio.Task[Any]] = {}
        self._cancel_callbacks: dict[str, RuntimeTaskCancelCallback] = {}
        self._completion_callbacks: dict[str, RuntimeTaskCompletionCallback] = {}
        self._runtime_status_events: set[tuple[str, str]] = set()
        self._completion_consumers: dict[str, int] = {}
        self._work = DurableWorkRuntime(control_plane.store)

    def start_task(
        self,
        *,
        kind: RuntimeTaskKind | str,
        owner_session_id: str,
        owner_turn_id: str,
        source_tool: str,
        task_factory: RuntimeTaskFactory,
        write_scope: str | None = None,
        notify_on_complete: bool = True,
        metadata: Mapping[str, Any] | None = None,
        task_id: str | None = None,
    ) -> RuntimeTaskRecord:
        task_kind = self._validate_background_kind(kind)
        normalized_scope = self._normalize_scope(write_scope)
        self._ensure_scope_available(normalized_scope)
        record = RuntimeTaskRecord(
            task_id=task_id or utc_id("task_"),
            kind=task_kind,
            owner_session_id=owner_session_id,
            owner_turn_id=owner_turn_id,
            source_tool=source_tool,
            write_scope=normalized_scope,
            status="queued",
            metadata=dict(metadata or {}),
            notify_on_complete=notify_on_complete,
        )
        self._submit_runtime_task(record)
        record.task = asyncio.create_task(self._run_record(record, task_factory), context=contextvars.Context())
        self._active_records[record.task_id] = record
        self._active_tasks[record.task_id] = record.task
        return record

    async def _run_record(self, record: RuntimeTaskRecord, task_factory: RuntimeTaskFactory) -> None:
        context = RuntimeTaskContext(self, record.task_id)
        try:
            record.status = "running"
            record.started_at = _now()
            self._append_runtime_status_event(record, "task.started")
            outcome = await task_factory(context)
            if isinstance(outcome, RuntimeTaskOutcome):
                if outcome.summary:
                    record.summary = outcome.summary
                if outcome.result_ref is not None:
                    record.result_ref = outcome.result_ref
                if outcome.metadata:
                    record.metadata.update(outcome.metadata)
            elif isinstance(outcome, Mapping):
                summary = outcome.get("summary")
                if summary is not None:
                    record.summary = str(summary)
                result_ref = outcome.get("result_ref")
                if result_ref is not None:
                    record.result_ref = str(result_ref)
                metadata = outcome.get("metadata")
                if isinstance(metadata, Mapping):
                    record.metadata.update(dict(metadata))
            elif outcome is not None and not record.summary:
                record.summary = str(outcome)
            if record.status not in TERMINAL_TASK_STATUSES and record.status != "blocked_needs_user":
                record.status = "succeeded"
                record.completed_at = _now()
                self._append_runtime_status_event(record, "task.succeeded")
            elif record.status in TERMINAL_TASK_STATUSES and record.completed_at is None:
                record.completed_at = _now()
                self._append_runtime_status_event(record, f"task.{record.status}")
        except asyncio.CancelledError:
            if record.status != "cancelled":
                record.status = "cancelled"
                record.summary = record.summary or "task cancelled"
                record.completed_at = _now()
            self._append_runtime_status_event(record, "task.cancelled")
            raise
        except Exception as exc:
            record.status = "failed"
            record.summary = str(exc)
            record.completed_at = _now()
            self.append_log(record.task_id, f"error: {exc}")
            self._append_runtime_status_event(record, "task.failed")
        finally:
            if record.status in TERMINAL_TASK_STATUSES or record.status == "blocked_needs_user":
                self._emit_completion_once(record)
            self._cancel_callbacks.pop(record.task_id, None)

    def subscribe(self, callback: RuntimeTaskCompletionCallback) -> Callable[[], None]:
        subscription_id = utc_id("task_sub_")
        self._completion_callbacks[subscription_id] = callback
        for event in self.pending_events():
            self._notify_completion_callback(callback, event)

        def unsubscribe() -> None:
            self._completion_callbacks.pop(subscription_id, None)

        return unsubscribe

    def pending_events(self) -> list[RuntimeTaskCompletionEvent]:
        return self._pending_completion_events()

    def pending_events_for_session(self, session_id: str) -> list[RuntimeTaskCompletionEvent]:
        return [event for event in self._pending_completion_events() if event.owner_session_id == session_id]

    def claim_pending_event(self, event_id: str, *, owner_id: str) -> DurableClaim | None:
        return self._work.claim(event_id, owner_id=owner_id)

    def ack_pending_event(self, claim: DurableClaim) -> bool:
        try:
            self._work.acknowledge(claim)
        except Exception:
            return False
        self.control_plane.ack_completion(claim.work_id)
        return True

    def ack_pending_event_id(self, event_id: str, *, claim_id: str) -> bool:
        rows = self.control_plane.store.query(
            RuntimeQuery(table="runtime_work_items", where={"work_id": event_id}, limit=1)
        ).rows
        if not rows:
            return False
        row = rows[0]
        if str(row.get("claim_id") or "") != claim_id:
            return False
        claim = DurableClaim(
            work_id=event_id,
            kind=str(row.get("kind") or "task.completion"),
            claim_id=claim_id,
            owner_id=str(row.get("owner_id") or "host.task_worker"),
            lease_expires_at=str(row.get("lease_expires_at") or ""),
            attempt=int(row.get("attempts") or 0),
        )
        return self.ack_pending_event(claim)

    def ack_pending_event_for_task(self, task_id: str) -> bool:
        acknowledged = False
        for event in self._pending_completion_events():
            if event.task_id != task_id:
                continue
            claim = self.claim_pending_event(event.event_id, owner_id="host.task_worker.wait")
            if claim is None:
                continue
            acknowledged = self.ack_pending_event(claim) or acknowledged
        return acknowledged

    def get(self, task_id: str) -> RuntimeTaskRecord:
        try:
            return self._record_from_projection(task_id)
        except KeyError as exc:
            raise KeyError(f"background task not found: {task_id}") from exc

    def list_tasks(
        self,
        *,
        owner_session_id: str | None = None,
        kind: str | None = None,
        include_completed: bool = True,
    ) -> list[RuntimeTaskRecord]:
        if kind is not None:
            self._validate_background_kind(kind)
        where: dict[str, Any] = {}
        if owner_session_id:
            where["owner_session_id"] = owner_session_id
        rows = self.control_plane.store.query(
            RuntimeQuery(table="tasks", where=where, order_by="created_at", limit=1000)
        ).rows
        records: list[RuntimeTaskRecord] = []
        for row in rows:
            row_kind = str(row.get("kind") or "")
            if row_kind not in BACKGROUND_TASK_KINDS:
                continue
            if kind is not None and row_kind != kind:
                continue
            records.append(self._record_from_projection(str(row["task_id"]), task_row=row))
        if not include_completed:
            records = [record for record in records if record.running]
        return sorted(records, key=lambda record: record.started_at or "")

    def log(self, task_id: str, *, tail: int | None = None) -> list[str]:
        self.get(task_id)
        rows = self.control_plane.store.query(
            RuntimeQuery(table="task_logs", where={"task_id": task_id}, order_by="seq", limit=10000)
        ).rows
        log = [str(row.get("text") or "") for row in rows]
        if tail is None:
            return log
        return log[-max(0, int(tail)) :]

    async def wait(
        self,
        task_id: str,
        *,
        timeout_seconds: int | float | None = None,
        consume_completion: bool = False,
    ) -> RuntimeTaskRecord:
        if consume_completion:
            self._begin_completion_consumer(task_id)
        completion_consumed = False
        try:
            self.get(task_id)
            task = self._active_tasks.get(task_id)
            if task is not None and not task.done():
                try:
                    await asyncio.wait_for(asyncio.shield(task), timeout=timeout_seconds)
                except asyncio.TimeoutError:
                    raise
                except asyncio.CancelledError:
                    raise
                except Exception:
                    pass
            record = self.get(task_id)
            if consume_completion and _is_completion_status(record.status):
                self.ack_pending_event_for_task(task_id)
                completion_consumed = True
            return record
        finally:
            if consume_completion:
                self._end_completion_consumer(task_id)
                if not completion_consumed:
                    try:
                        record = self.get(task_id)
                    except (KeyError, RuntimeTaskKindError):
                        record = None
                    if record is not None and _is_completion_status(record.status):
                        self._emit_completion_once(record)

    async def cancel(self, task_id: str) -> RuntimeTaskRecord:
        record = self.get(task_id)
        if record.status in TERMINAL_TASK_STATUSES:
            return record
        active_record = self._active_records.get(task_id)
        if active_record is None:
            self.control_plane.cancel(task_id)
            return self.get(task_id)
        record = active_record
        record.status = "cancelled"
        record.summary = record.summary or "task cancelled"
        record.completed_at = _now()
        self._append_runtime_status_event(record, "task.cancelled")
        callback = self._cancel_callbacks.get(task_id)
        if callback is not None:
            value = callback()
            if inspect.isawaitable(value):
                await value
        task = self._active_tasks.get(task_id)
        if task is not None and not task.done():
            task.cancel()
            with_context = asyncio.gather(task, return_exceptions=True)
            try:
                await asyncio.wait_for(with_context, timeout=5)
            except asyncio.TimeoutError:
                pass
        self._emit_completion_once(record)
        return self.get(task_id)

    async def drain(self) -> None:
        while True:
            tasks = [task for task in self._active_tasks.values() if not task.done()]
            if not tasks:
                return
            await asyncio.gather(*tasks, return_exceptions=True)

    @property
    def active_count(self) -> int:
        return sum(1 for task in self._active_tasks.values() if not task.done())

    def append_log(self, task_id: str, text: str) -> None:
        record = self._active_records.get(task_id) or self.get(task_id)
        self.control_plane.append_task_log(task_id, text, source=self._runtime_source(record))
        record.log_tail = self._bounded_tail(self.log(task_id))

    def set_summary(self, task_id: str, summary: str) -> None:
        self._active_record(task_id).summary = str(summary)

    def set_result_ref(self, task_id: str, result_ref: str | None) -> None:
        self._active_record(task_id).result_ref = result_ref

    def update_metadata(self, task_id: str, values: Mapping[str, Any]) -> None:
        self._active_record(task_id).metadata.update(dict(values))

    def set_cancel_callback(self, task_id: str, callback: RuntimeTaskCancelCallback) -> None:
        self.get(task_id)
        self._cancel_callbacks[task_id] = callback

    def mark_blocked(self, task_id: str, summary: str, *, metadata: Mapping[str, Any] | None = None) -> None:
        record = self._active_record(task_id)
        record.status = "blocked_needs_user"
        record.summary = str(summary)
        if metadata:
            record.metadata.update(dict(metadata))
        self._append_runtime_status_event(record, "task.blocked")

    def _submit_runtime_task(self, record: RuntimeTaskRecord) -> None:
        payload = {
            "task_id": record.task_id,
            "owner_session_id": record.owner_session_id,
            "owner_turn_id": record.owner_turn_id,
            "core_id": record.metadata.get("core_id") or record.metadata.get("child_core_id"),
            "notify_policy": "completion_event" if record.notify_on_complete else "silent",
            "source_tool": record.source_tool,
            "write_scope": record.write_scope,
            "metadata": dict(record.metadata),
        }
        self.control_plane.submit_task(
            TaskSpec(
                kind=record.kind,
                payload=payload,
                idempotency_key=f"task:{record.task_id}:submitted",
            ),
            source=TaskSource(
                actor="host.task_worker",
                session_id=record.owner_session_id,
                turn_id=record.owner_turn_id,
                core_id=payload["core_id"],
                task_id=record.task_id,
                metadata={"kind": record.kind, "source_tool": record.source_tool},
            ),
        )

    def _append_runtime_status_event(self, record: RuntimeTaskRecord, event_type: str) -> None:
        if (record.task_id, event_type) in self._runtime_status_events:
            return
        self._runtime_status_events.add((record.task_id, event_type))
        source = self._runtime_source(record)
        if event_type == "task.started":
            self.control_plane.mark_started(record.task_id, source=source)
        elif event_type == "task.succeeded":
            self.control_plane.succeed(
                record.task_id,
                result_ref=record.result_ref,
                summary=record.summary,
                metadata=dict(record.metadata),
                source=source,
            )
        elif event_type == "task.failed":
            self.control_plane.fail(
                record.task_id,
                error=record.summary or "task failed",
                summary=record.summary,
                metadata=dict(record.metadata),
                source=source,
            )
        elif event_type == "task.cancelled":
            self.control_plane.cancel(
                record.task_id,
                summary=record.summary or "task cancelled",
                metadata=dict(record.metadata),
                source=source,
            )
        elif event_type == "task.blocked":
            self.control_plane.block(
                record.task_id,
                summary=record.summary,
                metadata=dict(record.metadata),
                source=source,
            )
        elif event_type == "task.lost":
            self.control_plane.fail(
                record.task_id,
                error=record.summary or "task lost",
                summary=record.summary,
                metadata=dict(record.metadata),
                source=source,
            )
        else:
            raise ValueError(f"unsupported task lifecycle event: {event_type}")

    def _runtime_source(self, record: RuntimeTaskRecord) -> TaskSource:
        return TaskSource(
            actor="host.task_worker",
            session_id=record.owner_session_id,
            turn_id=record.owner_turn_id,
            core_id=record.metadata.get("core_id") or record.metadata.get("child_core_id"),
            task_id=record.task_id,
            metadata={"kind": record.kind, "source_tool": record.source_tool},
        )

    def _ensure_scope_available(self, write_scope: str | None) -> None:
        if not write_scope:
            return
        for task in self.list_tasks(include_completed=False):
            if task.write_scope == write_scope and task.running:
                raise RuntimeTaskConflictError(f"background task write_scope is already active: {write_scope}")

    def _emit_completion_once(self, record: RuntimeTaskRecord) -> None:
        if not record.notify_on_complete or self._completion_ready_event_for_task(record.task_id) is not None:
            return
        if self._completion_consumers.get(record.task_id, 0) > 0:
            return
        event = RuntimeTaskCompletionEvent(
            event_id=utc_id("task_event_"),
            task_id=record.task_id,
            kind=record.kind,
            owner_session_id=record.owner_session_id,
            owner_turn_id=record.owner_turn_id,
            source_tool=record.source_tool,
            status=record.status,
            summary=record.summary,
            log_tail=tuple(record.log_tail),
            result_ref=record.result_ref,
            metadata=dict(record.metadata),
        )
        row = self.control_plane.emit_completion_ready(
            event_id=event.event_id,
            task_id=record.task_id,
            payload=event.to_payload(),
            source=self._runtime_source(record),
            idempotency_key=f"task:{record.task_id}:completion_ready",
        )
        event = RuntimeTaskCompletionEvent.from_runtime_event(row)
        for callback in list(self._completion_callbacks.values()):
            self._notify_completion_callback(callback, event)

    def _notify_completion_callback(
        self,
        callback: RuntimeTaskCompletionCallback,
        event: RuntimeTaskCompletionEvent,
    ) -> None:
        try:
            value = callback(event)
            if inspect.isawaitable(value):
                asyncio.create_task(value)
        except RuntimeError:
            return

    def _begin_completion_consumer(self, task_id: str) -> None:
        self._completion_consumers[task_id] = self._completion_consumers.get(task_id, 0) + 1

    def _end_completion_consumer(self, task_id: str) -> None:
        count = self._completion_consumers.get(task_id, 0)
        if count <= 1:
            self._completion_consumers.pop(task_id, None)
            return
        self._completion_consumers[task_id] = count - 1

    def _pending_completion_events(self) -> list[RuntimeTaskCompletionEvent]:
        rows = self.control_plane.store.query(
            RuntimeQuery(
                table="runtime_events",
                where={"aggregate_type": "task_completion"},
                order_by="seq",
                limit=10_000,
            )
        ).rows
        cleared = {
            str(row.get("aggregate_id"))
            for row in rows
            if row.get("type") == "task.completion_acknowledged"
        }
        work_rows = self.control_plane.store.query(
            RuntimeQuery(table="runtime_work_items", where={"kind": "task.completion"}, order_by="created_at", limit=10_000)
        ).rows
        non_pending = {
            str(row["work_id"])
            for row in work_rows
            if row.get("status") not in {"queued", "retry_scheduled"}
        }
        events = [
            RuntimeTaskCompletionEvent.from_runtime_event(row)
            for row in rows
            if row.get("type") == "task.completion_ready"
            and str(row.get("aggregate_id")) not in cleared
            and str(row.get("aggregate_id")) not in non_pending
        ]
        return events

    def _completion_ready_event_for_task(self, task_id: str) -> RuntimeTaskCompletionEvent | None:
        for event in self._pending_completion_events():
            if event.task_id == task_id:
                return event
        rows = self.control_plane.store.query(
            RuntimeQuery(
                table="runtime_events",
                where={"aggregate_type": "task_completion"},
                order_by="seq",
                limit=10_000,
            )
        ).rows
        for row in rows:
            if row.get("type") != "task.completion_ready":
                continue
            event = RuntimeTaskCompletionEvent.from_runtime_event(row)
            if event.task_id == task_id:
                return event
        return None

    def _active_record(self, task_id: str) -> RuntimeTaskRecord:
        record = self._active_records.get(task_id)
        if record is None:
            raise KeyError(f"task is not active in this worker: {task_id}")
        return record

    def _record_from_projection(self, task_id: str, *, task_row: Mapping[str, Any] | None = None) -> RuntimeTaskRecord:
        if task_row is None:
            rows = self.control_plane.store.query(RuntimeQuery(table="tasks", where={"task_id": task_id}, limit=1)).rows
            if not rows:
                raise KeyError(task_id)
            task_row = rows[0]
        kind = self._validate_background_kind(str(task_row.get("kind") or ""))
        events = self.control_plane.store.query(
            RuntimeQuery(
                table="runtime_events",
                where={"aggregate_type": "task", "aggregate_id": task_id},
                order_by="seq",
                limit=1000,
            )
        ).rows
        submitted = next((event for event in events if event.get("type") == "task.submitted"), {})
        submitted_payload = submitted.get("payload") if isinstance(submitted.get("payload"), Mapping) else {}
        action = submitted_payload.get("action") if isinstance(submitted_payload.get("action"), Mapping) else {}
        source = task_row.get("source") if isinstance(task_row.get("source"), Mapping) else {}
        source_metadata = source.get("metadata") if isinstance(source.get("metadata"), Mapping) else {}
        metadata: dict[str, Any] = {}
        if isinstance(action.get("metadata"), Mapping):
            metadata.update(dict(action["metadata"]))
        summary = ""
        source_tool = str(action.get("source_tool") or source_metadata.get("source_tool") or "")
        write_scope = action.get("write_scope")
        for event in events:
            payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
            if isinstance(payload.get("metadata"), Mapping):
                metadata.update(dict(payload["metadata"]))
            if payload.get("summary") is not None:
                summary = str(payload.get("summary") or "")
            if payload.get("source_tool") is not None:
                source_tool = str(payload.get("source_tool") or "")
        task = self._active_tasks.get(task_id)
        return RuntimeTaskRecord(
            task_id=task_id,
            kind=kind,
            owner_session_id=str(task_row.get("owner_session_id") or ""),
            owner_turn_id=str(task_row.get("owner_turn_id") or ""),
            source_tool=source_tool,
            write_scope=str(write_scope) if write_scope else None,
            status=str(task_row.get("status") or "queued"),  # type: ignore[arg-type]
            started_at=task_row.get("started_at"),
            completed_at=task_row.get("completed_at"),
            summary=summary,
            log_tail=self._bounded_tail(self._log_from_store(task_id)),
            result_ref=task_row.get("result_ref"),
            metadata=metadata,
            notify_on_complete=str(task_row.get("notify_policy") or "") != "silent",
            task=task,
        )

    def _log_from_store(self, task_id: str) -> list[str]:
        rows = self.control_plane.store.query(
            RuntimeQuery(table="task_logs", where={"task_id": task_id}, order_by="seq", limit=10000)
        ).rows
        return [str(row.get("text") or "") for row in rows]

    def _bounded_tail(self, log: list[str]) -> list[str]:
        tail = list(log[-self.log_tail_lines :])
        while tail and sum(len(line) + 1 for line in tail) > self.log_tail_chars:
            tail.pop(0)
        return tail

    def _normalize_scope(self, scope: str | None) -> str | None:
        value = str(scope or "").strip()
        return value or None

    def _validate_background_kind(self, kind: str) -> RuntimeTaskKind:
        if kind == "terminal.exec":
            return "terminal.exec"
        if kind == "evolver.run":
            return "evolver.run"
        if kind == "agent.spawn":
            return "agent.spawn"
        raise RuntimeTaskKindError(f"unsupported background task kind: {kind}")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _is_completion_status(status: RuntimeTaskStatus) -> bool:
    return status in TERMINAL_TASK_STATUSES or status == "blocked_needs_user"
