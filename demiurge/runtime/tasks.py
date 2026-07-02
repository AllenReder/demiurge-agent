from __future__ import annotations

import asyncio
import contextvars
import inspect
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable, Literal, Mapping

from demiurge.runtime.control import ActionSource, ActionSpec, RuntimeControlPlane
from demiurge.runtime.store import RuntimeEvent, RuntimeQuery
from demiurge.util import utc_id


RuntimeTaskStatus = Literal["queued", "running", "blocked_needs_user", "succeeded", "failed", "cancelled", "lost"]
TERMINAL_TASK_STATUSES = {"succeeded", "failed", "cancelled", "lost"}

RuntimeTaskCancelCallback = Callable[[], Any | Awaitable[Any]]
RuntimeTaskCompletionCallback = Callable[["RuntimeTaskCompletionEvent"], Any | Awaitable[Any]]
RuntimeTaskFactory = Callable[["RuntimeTaskContext"], Awaitable[Any]]


class RuntimeTaskConflictError(RuntimeError):
    pass


@dataclass(slots=True)
class RuntimeTaskOutcome:
    summary: str = ""
    result_ref: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RuntimeTaskRecord:
    job_id: str
    backend: str
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
            "job_id": self.job_id,
            "backend": self.backend,
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
    job_id: str
    backend: str
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
            "[SYSTEM: Background job event]",
            f"job_id: {self.job_id}",
            f"backend: {self.backend}",
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
                "Respond to the user with a concise status update. Do not rerun the job. "
                "If more detail is needed, use the job tool to inspect the job.",
            ]
        )
        return "\n".join(lines).strip()

    def to_metadata(self) -> dict[str, Any]:
        return {
            "synthetic": True,
            "trigger": "background_job",
            "event_id": self.event_id,
            "job_id": self.job_id,
            "backend": self.backend,
            "source_tool": self.source_tool,
            "job_status": self.status,
            "owner_turn_id": self.owner_turn_id,
            "result_ref": self.result_ref,
        }

    def to_payload(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "backend": self.backend,
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
            job_id=str(payload.get("job_id") or ""),
            backend=str(payload.get("backend") or ""),
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
    def __init__(self, runtime: "RuntimeTaskWorker", job_id: str):
        self.runtime = runtime
        self.job_id = job_id

    def append_log(self, text: str) -> None:
        self.runtime.append_log(self.job_id, text)

    def set_summary(self, summary: str) -> None:
        self.runtime.set_summary(self.job_id, summary)

    def set_result_ref(self, result_ref: str | None) -> None:
        self.runtime.set_result_ref(self.job_id, result_ref)

    def update_metadata(self, values: Mapping[str, Any]) -> None:
        self.runtime.update_metadata(self.job_id, values)

    def set_cancel_callback(self, callback: RuntimeTaskCancelCallback) -> None:
        self.runtime.set_cancel_callback(self.job_id, callback)

    def mark_blocked(self, summary: str, *, metadata: Mapping[str, Any] | None = None) -> None:
        self.runtime.mark_blocked(self.job_id, summary, metadata=metadata)


class RuntimeTaskWorker:
    """In-process worker for RuntimeControlPlane tasks.

    SQLite projections are the read source of truth. This object only owns
    active asyncio handles, cancellation callbacks, and live completion
    subscribers; pending completion events are reconstructed from SQLite.
    """

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

    def start_task(
        self,
        *,
        backend: str,
        owner_session_id: str,
        owner_turn_id: str,
        source_tool: str,
        task_factory: RuntimeTaskFactory,
        write_scope: str | None = None,
        notify_on_complete: bool = True,
        metadata: Mapping[str, Any] | None = None,
        job_id: str | None = None,
    ) -> RuntimeTaskRecord:
        normalized_scope = self._normalize_scope(write_scope)
        self._ensure_scope_available(normalized_scope)
        record = RuntimeTaskRecord(
            job_id=job_id or utc_id("job_"),
            backend=str(backend),
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
        self._active_records[record.job_id] = record
        self._active_tasks[record.job_id] = record.task
        return record

    async def _run_record(self, record: RuntimeTaskRecord, task_factory: RuntimeTaskFactory) -> None:
        context = RuntimeTaskContext(self, record.job_id)
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
                record.summary = record.summary or "job cancelled"
                record.completed_at = _now()
            self._append_runtime_status_event(record, "task.cancelled")
            raise
        except Exception as exc:
            record.status = "failed"
            record.summary = str(exc)
            record.completed_at = _now()
            self.append_log(record.job_id, f"error: {exc}")
            self._append_runtime_status_event(record, "task.failed")
        finally:
            if record.status in TERMINAL_TASK_STATUSES or record.status == "blocked_needs_user":
                self._emit_completion_once(record)
            self._cancel_callbacks.pop(record.job_id, None)

    def subscribe(self, callback: RuntimeTaskCompletionCallback) -> Callable[[], None]:
        subscription_id = utc_id("job_sub_")
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

    def clear_pending_event(self, event_id: str) -> bool:
        if not any(event.event_id == event_id for event in self._pending_completion_events()):
            return False
        self.control_plane.store.append(
            [
                RuntimeEvent(
                    type="task.completion_cleared",
                    aggregate_type="task_completion",
                    aggregate_id=event_id,
                    payload={"event_id": event_id, "status": "cleared"},
                )
            ],
            idempotency_key=f"task_completion:{event_id}:cleared",
        )
        return True

    def get(self, job_id: str) -> RuntimeTaskRecord:
        try:
            return self._record_from_projection(job_id)
        except KeyError as exc:
            raise KeyError(f"job not found: {job_id}") from exc

    def list_tasks(
        self,
        *,
        owner_session_id: str | None = None,
        backend: str | None = None,
        include_completed: bool = True,
    ) -> list[RuntimeTaskRecord]:
        where: dict[str, Any] = {}
        if owner_session_id:
            where["owner_session_id"] = owner_session_id
        rows = self.control_plane.store.query(
            RuntimeQuery(table="tasks", where=where, order_by="created_at", limit=1000)
        ).rows
        jobs = [self._record_from_projection(str(row["task_id"]), task_row=row) for row in rows]
        if backend:
            jobs = [job for job in jobs if job.backend == backend]
        if not include_completed:
            jobs = [job for job in jobs if job.running]
        return sorted(jobs, key=lambda job: job.started_at or "")

    def log(self, job_id: str, *, tail: int | None = None) -> list[str]:
        self.get(job_id)
        rows = self.control_plane.store.query(
            RuntimeQuery(table="task_logs", where={"task_id": job_id}, order_by="seq", limit=10000)
        ).rows
        log = [str(row.get("text") or "") for row in rows]
        if tail is None:
            return log
        return log[-max(0, int(tail)) :]

    async def wait(self, job_id: str, *, timeout_seconds: int | float | None = None) -> RuntimeTaskRecord:
        record = self.get(job_id)
        task = self._active_tasks.get(job_id)
        if task is None or task.done():
            return self.get(job_id)
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            raise
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        return self.get(job_id)

    async def cancel(self, job_id: str) -> RuntimeTaskRecord:
        record = self.get(job_id)
        if record.status in TERMINAL_TASK_STATUSES:
            return record
        active_record = self._active_records.get(job_id)
        if active_record is None:
            self.control_plane.control(job_id, "cancel")
            return self.get(job_id)
        record = active_record
        record.status = "cancelled"
        record.summary = record.summary or "job cancelled"
        record.completed_at = _now()
        self._append_runtime_status_event(record, "task.cancelled")
        callback = self._cancel_callbacks.get(job_id)
        if callback is not None:
            value = callback()
            if inspect.isawaitable(value):
                await value
        task = self._active_tasks.get(job_id)
        if task is not None and not task.done():
            task.cancel()
            with_context = asyncio.gather(task, return_exceptions=True)
            try:
                await asyncio.wait_for(with_context, timeout=5)
            except asyncio.TimeoutError:
                pass
        self._emit_completion_once(record)
        return self.get(job_id)

    async def drain(self) -> None:
        while True:
            tasks = [task for task in self._active_tasks.values() if not task.done()]
            if not tasks:
                return
            await asyncio.gather(*tasks, return_exceptions=True)

    @property
    def active_count(self) -> int:
        return len(self.list_tasks(include_completed=False))

    def append_log(self, job_id: str, text: str) -> None:
        record = self._active_records.get(job_id) or self.get(job_id)
        lines = str(text).splitlines() or [str(text)]
        self._append_runtime_events(
            record,
            [
                RuntimeEvent(
                    type="task.log",
                    aggregate_type="task",
                    aggregate_id=record.job_id,
                    actor=self._runtime_actor(record),
                    payload={"stream": "stdout", "text": line},
                )
                for line in lines
            ],
        )
        record.log_tail = self._bounded_tail(self.log(job_id))

    def set_summary(self, job_id: str, summary: str) -> None:
        self._active_record(job_id).summary = str(summary)

    def set_result_ref(self, job_id: str, result_ref: str | None) -> None:
        self._active_record(job_id).result_ref = result_ref

    def update_metadata(self, job_id: str, values: Mapping[str, Any]) -> None:
        self._active_record(job_id).metadata.update(dict(values))

    def set_cancel_callback(self, job_id: str, callback: RuntimeTaskCancelCallback) -> None:
        self.get(job_id)
        self._cancel_callbacks[job_id] = callback

    def mark_blocked(self, job_id: str, summary: str, *, metadata: Mapping[str, Any] | None = None) -> None:
        record = self._active_record(job_id)
        record.status = "blocked_needs_user"
        record.summary = str(summary)
        if metadata:
            record.metadata.update(dict(metadata))
        self._append_runtime_status_event(record, "task.blocked")

    def _submit_runtime_task(self, record: RuntimeTaskRecord) -> None:
        payload = {
            "task_id": record.job_id,
            "owner_session_id": record.owner_session_id,
            "owner_turn_id": record.owner_turn_id,
            "core_id": record.metadata.get("core_id") or record.metadata.get("child_core_id"),
            "notify_policy": "completion_event" if record.notify_on_complete else "silent",
            "backend": record.backend,
            "source_tool": record.source_tool,
            "write_scope": record.write_scope,
            "metadata": dict(record.metadata),
        }
        self.control_plane.submit(
            ActionSpec(
                kind=_action_kind_for_backend(record.backend),
                payload=payload,
                idempotency_key=f"task:{record.job_id}:submitted",
            ),
            source=ActionSource(
                actor="host.task_worker",
                session_id=record.owner_session_id,
                turn_id=record.owner_turn_id,
                core_id=payload["core_id"],
                metadata={"backend": record.backend, "source_tool": record.source_tool},
            ),
        )

    def _append_runtime_status_event(self, record: RuntimeTaskRecord, event_type: str) -> None:
        if (record.job_id, event_type) in self._runtime_status_events:
            return
        self._runtime_status_events.add((record.job_id, event_type))
        payload: dict[str, Any] = {
            "status": record.status,
            "backend": record.backend,
            "source_tool": record.source_tool,
            "summary": record.summary,
            "result_ref": record.result_ref,
            "metadata": dict(record.metadata),
        }
        if event_type == "task.failed":
            payload["error"] = {"message": record.summary or "job failed"}
        elif event_type == "task.cancelled":
            payload["error"] = {"message": record.summary or "job cancelled"}
        self._append_runtime_events(
            record,
            [
                RuntimeEvent(
                    type=event_type,
                    aggregate_type="task",
                    aggregate_id=record.job_id,
                    actor=self._runtime_actor(record),
                    payload=payload,
                )
            ],
        )

    def _append_runtime_events(self, record: RuntimeTaskRecord, events: list[RuntimeEvent]) -> None:
        if not events:
            return
        self.control_plane.store.append(events)

    def _runtime_actor(self, record: RuntimeTaskRecord) -> dict[str, Any]:
        return {
            "actor": "host.task_worker",
            "session_id": record.owner_session_id,
            "turn_id": record.owner_turn_id,
            "core_id": record.metadata.get("core_id") or record.metadata.get("child_core_id"),
            "metadata": {"backend": record.backend, "source_tool": record.source_tool},
        }

    def _ensure_scope_available(self, write_scope: str | None) -> None:
        if not write_scope:
            return
        for job in self.list_tasks(include_completed=False):
            if job.write_scope == write_scope and job.running:
                raise RuntimeTaskConflictError(f"background job write_scope is already active: {write_scope}")

    def _emit_completion_once(self, record: RuntimeTaskRecord) -> None:
        if not record.notify_on_complete or self._completion_ready_event_for_task(record.job_id) is not None:
            return
        event = RuntimeTaskCompletionEvent(
            event_id=utc_id("job_event_"),
            job_id=record.job_id,
            backend=record.backend,
            owner_session_id=record.owner_session_id,
            owner_turn_id=record.owner_turn_id,
            source_tool=record.source_tool,
            status=record.status,
            summary=record.summary,
            log_tail=tuple(record.log_tail),
            result_ref=record.result_ref,
            metadata=dict(record.metadata),
        )
        result = self.control_plane.store.append(
            [
                RuntimeEvent(
                    type="task.completion_ready",
                    aggregate_type="task_completion",
                    aggregate_id=event.event_id,
                    actor=self._runtime_actor(record),
                    payload=event.to_payload(),
                )
            ],
            idempotency_key=f"task:{record.job_id}:completion_ready",
        )
        event = RuntimeTaskCompletionEvent.from_runtime_event(result.events[-1])
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
            if row.get("type") == "task.completion_cleared"
        }
        events = [
            RuntimeTaskCompletionEvent.from_runtime_event(row)
            for row in rows
            if row.get("type") == "task.completion_ready" and str(row.get("aggregate_id")) not in cleared
        ]
        return events

    def _completion_ready_event_for_task(self, job_id: str) -> RuntimeTaskCompletionEvent | None:
        for event in self._pending_completion_events():
            if event.job_id == job_id:
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
            if event.job_id == job_id:
                return event
        return None

    def _active_record(self, job_id: str) -> RuntimeTaskRecord:
        record = self._active_records.get(job_id)
        if record is None:
            raise KeyError(f"task is not active in this worker: {job_id}")
        return record

    def _record_from_projection(self, job_id: str, *, task_row: Mapping[str, Any] | None = None) -> RuntimeTaskRecord:
        if task_row is None:
            rows = self.control_plane.store.query(RuntimeQuery(table="tasks", where={"task_id": job_id}, limit=1)).rows
            if not rows:
                raise KeyError(job_id)
            task_row = rows[0]
        events = self.control_plane.store.query(
            RuntimeQuery(
                table="runtime_events",
                where={"aggregate_type": "task", "aggregate_id": job_id},
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
        backend = str(action.get("backend") or source_metadata.get("backend") or _backend_for_kind(str(task_row.get("kind") or "")))
        write_scope = action.get("write_scope")
        for event in events:
            payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
            if isinstance(payload.get("metadata"), Mapping):
                metadata.update(dict(payload["metadata"]))
            if payload.get("summary") is not None:
                summary = str(payload.get("summary") or "")
            if payload.get("source_tool") is not None:
                source_tool = str(payload.get("source_tool") or "")
            if payload.get("backend") is not None:
                backend = str(payload.get("backend") or "")
        task = self._active_tasks.get(job_id)
        return RuntimeTaskRecord(
            job_id=job_id,
            backend=backend,
            owner_session_id=str(task_row.get("owner_session_id") or ""),
            owner_turn_id=str(task_row.get("owner_turn_id") or ""),
            source_tool=source_tool,
            write_scope=str(write_scope) if write_scope else None,
            status=str(task_row.get("status") or "queued"),  # type: ignore[arg-type]
            started_at=task_row.get("started_at"),
            completed_at=task_row.get("completed_at"),
            summary=summary,
            log_tail=self._bounded_tail(self._log_from_store(job_id)),
            result_ref=task_row.get("result_ref"),
            metadata=metadata,
            notify_on_complete=str(task_row.get("notify_policy") or "") != "silent",
            task=task,
        )

    def _log_from_store(self, job_id: str) -> list[str]:
        rows = self.control_plane.store.query(
            RuntimeQuery(table="task_logs", where={"task_id": job_id}, order_by="seq", limit=10000)
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


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _action_kind_for_backend(backend: str):
    if backend == "terminal":
        return "terminal.exec"
    if backend == "evolve":
        return "evolver.run"
    if backend == "agent":
        return "agent.spawn"
    return "tool.call"


def _backend_for_kind(kind: str) -> str:
    if kind == "terminal.exec":
        return "terminal"
    if kind == "evolver.run":
        return "evolve"
    if kind == "agent.spawn":
        return "agent"
    return "tool"
