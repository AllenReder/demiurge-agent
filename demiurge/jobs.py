from __future__ import annotations

import asyncio
import contextvars
import inspect
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable, Literal, Mapping

from demiurge.util import utc_id


JobStatus = Literal["queued", "running", "blocked_needs_user", "succeeded", "failed", "cancelled", "lost"]
TERMINAL_JOB_STATUSES = {"succeeded", "failed", "cancelled", "lost"}

JobCancelCallback = Callable[[], Any | Awaitable[Any]]
JobCompletionCallback = Callable[["JobCompletionEvent"], Any | Awaitable[Any]]
JobTaskFactory = Callable[["JobContext"], Awaitable[Any]]


class JobConflictError(RuntimeError):
    pass


@dataclass(slots=True)
class JobOutcome:
    summary: str = ""
    result_ref: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class JobRecord:
    job_id: str
    backend: str
    owner_session_id: str
    owner_turn_id: str
    source_tool: str
    write_scope: str | None
    status: JobStatus
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
class JobCompletionEvent:
    event_id: str
    job_id: str
    backend: str
    owner_session_id: str
    owner_turn_id: str
    source_tool: str
    status: JobStatus
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


class JobContext:
    def __init__(self, runtime: "JobRuntime", job_id: str):
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

    def set_cancel_callback(self, callback: JobCancelCallback) -> None:
        self.runtime.set_cancel_callback(self.job_id, callback)

    def mark_blocked(self, summary: str, *, metadata: Mapping[str, Any] | None = None) -> None:
        self.runtime.mark_blocked(self.job_id, summary, metadata=metadata)


class JobRuntime:
    """Host-owned in-memory background job registry and completion bus."""

    def __init__(
        self,
        *,
        max_log_lines: int = 4000,
        log_tail_lines: int = 40,
        log_tail_chars: int = 8000,
    ):
        self.max_log_lines = max_log_lines
        self.log_tail_lines = log_tail_lines
        self.log_tail_chars = log_tail_chars
        self._jobs: dict[str, JobRecord] = {}
        self._logs: dict[str, list[str]] = {}
        self._cancel_callbacks: dict[str, JobCancelCallback] = {}
        self._completion_callbacks: dict[str, JobCompletionCallback] = {}
        self._pending_events: dict[str, list[JobCompletionEvent]] = {}
        self._emitted_jobs: set[str] = set()

    def start_task(
        self,
        *,
        backend: str,
        owner_session_id: str,
        owner_turn_id: str,
        source_tool: str,
        task_factory: JobTaskFactory,
        write_scope: str | None = None,
        notify_on_complete: bool = True,
        metadata: Mapping[str, Any] | None = None,
        job_id: str | None = None,
    ) -> JobRecord:
        normalized_scope = self._normalize_scope(write_scope)
        self._ensure_scope_available(normalized_scope)
        record = JobRecord(
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
        self._jobs[record.job_id] = record
        self._logs[record.job_id] = []
        record.task = asyncio.create_task(self._run_record(record, task_factory), context=contextvars.Context())
        return record

    async def _run_record(self, record: JobRecord, task_factory: JobTaskFactory) -> None:
        context = JobContext(self, record.job_id)
        try:
            record.status = "running"
            record.started_at = _now()
            outcome = await task_factory(context)
            if isinstance(outcome, JobOutcome):
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
            if record.status not in TERMINAL_JOB_STATUSES and record.status != "blocked_needs_user":
                record.status = "succeeded"
                record.completed_at = _now()
            elif record.status in TERMINAL_JOB_STATUSES and record.completed_at is None:
                record.completed_at = _now()
        except asyncio.CancelledError:
            if record.status != "cancelled":
                record.status = "cancelled"
                record.summary = record.summary or "job cancelled"
                record.completed_at = _now()
            raise
        except Exception as exc:
            record.status = "failed"
            record.summary = str(exc)
            record.completed_at = _now()
            self.append_log(record.job_id, f"error: {exc}")
        finally:
            if record.status in TERMINAL_JOB_STATUSES or record.status == "blocked_needs_user":
                self._emit_completion_once(record)
            self._cancel_callbacks.pop(record.job_id, None)

    def subscribe(self, callback: JobCompletionCallback) -> Callable[[], None]:
        subscription_id = utc_id("job_sub_")
        self._completion_callbacks[subscription_id] = callback

        def unsubscribe() -> None:
            self._completion_callbacks.pop(subscription_id, None)

        return unsubscribe

    def pending_events_for_session(self, session_id: str) -> list[JobCompletionEvent]:
        return list(self._pending_events.get(session_id, []))

    def clear_pending_event(self, event_id: str) -> bool:
        for session_id, events in list(self._pending_events.items()):
            remaining = [event for event in events if event.event_id != event_id]
            if len(remaining) != len(events):
                if remaining:
                    self._pending_events[session_id] = remaining
                else:
                    self._pending_events.pop(session_id, None)
                return True
        return False

    def get(self, job_id: str) -> JobRecord:
        try:
            return self._jobs[job_id]
        except KeyError as exc:
            raise KeyError(f"job not found: {job_id}") from exc

    def list_jobs(
        self,
        *,
        owner_session_id: str | None = None,
        backend: str | None = None,
        include_completed: bool = True,
    ) -> list[JobRecord]:
        jobs = list(self._jobs.values())
        if owner_session_id:
            jobs = [job for job in jobs if job.owner_session_id == owner_session_id]
        if backend:
            jobs = [job for job in jobs if job.backend == backend]
        if not include_completed:
            jobs = [job for job in jobs if job.running]
        return sorted(jobs, key=lambda job: job.started_at or "")

    def log(self, job_id: str, *, tail: int | None = None) -> list[str]:
        self.get(job_id)
        log = list(self._logs.get(job_id, []))
        if tail is None:
            return log
        return log[-max(0, int(tail)) :]

    async def wait(self, job_id: str, *, timeout_seconds: int | float | None = None) -> JobRecord:
        record = self.get(job_id)
        task = record.task
        if task is None or task.done():
            return record
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            raise
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        return record

    async def cancel(self, job_id: str) -> JobRecord:
        record = self.get(job_id)
        if record.status in TERMINAL_JOB_STATUSES:
            return record
        record.status = "cancelled"
        record.summary = record.summary or "job cancelled"
        record.completed_at = _now()
        callback = self._cancel_callbacks.get(job_id)
        if callback is not None:
            value = callback()
            if inspect.isawaitable(value):
                await value
        task = record.task
        if task is not None and not task.done():
            task.cancel()
            with_context = asyncio.gather(task, return_exceptions=True)
            try:
                await asyncio.wait_for(with_context, timeout=5)
            except asyncio.TimeoutError:
                pass
        self._emit_completion_once(record)
        return record

    async def drain(self) -> None:
        while True:
            tasks = [job.task for job in self._jobs.values() if job.task is not None and not job.task.done()]
            if not tasks:
                return
            await asyncio.gather(*tasks, return_exceptions=True)

    @property
    def active_count(self) -> int:
        return sum(1 for job in self._jobs.values() if job.running)

    def append_log(self, job_id: str, text: str) -> None:
        record = self.get(job_id)
        lines = str(text).splitlines() or [str(text)]
        log = self._logs.setdefault(job_id, [])
        for line in lines:
            log.append(line)
        if len(log) > self.max_log_lines:
            del log[: len(log) - self.max_log_lines]
        record.log_tail = self._bounded_tail(log)

    def set_summary(self, job_id: str, summary: str) -> None:
        self.get(job_id).summary = str(summary)

    def set_result_ref(self, job_id: str, result_ref: str | None) -> None:
        self.get(job_id).result_ref = result_ref

    def update_metadata(self, job_id: str, values: Mapping[str, Any]) -> None:
        self.get(job_id).metadata.update(dict(values))

    def set_cancel_callback(self, job_id: str, callback: JobCancelCallback) -> None:
        self.get(job_id)
        self._cancel_callbacks[job_id] = callback

    def mark_blocked(self, job_id: str, summary: str, *, metadata: Mapping[str, Any] | None = None) -> None:
        record = self.get(job_id)
        record.status = "blocked_needs_user"
        record.summary = str(summary)
        if metadata:
            record.metadata.update(dict(metadata))

    def _ensure_scope_available(self, write_scope: str | None) -> None:
        if not write_scope:
            return
        for job in self._jobs.values():
            if job.write_scope == write_scope and job.running:
                raise JobConflictError(f"background job write_scope is already active: {write_scope}")

    def _emit_completion_once(self, record: JobRecord) -> None:
        if not record.notify_on_complete or record.job_id in self._emitted_jobs:
            return
        self._emitted_jobs.add(record.job_id)
        event = JobCompletionEvent(
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
        self._pending_events.setdefault(event.owner_session_id, []).append(event)
        for callback in list(self._completion_callbacks.values()):
            try:
                value = callback(event)
                if inspect.isawaitable(value):
                    asyncio.create_task(value)
            except RuntimeError:
                continue

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
