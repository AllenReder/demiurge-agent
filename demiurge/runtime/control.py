from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from demiurge.runtime.durable_work import DurableWorkSpec, durable_work_enqueued_event
from demiurge.runtime.store import RuntimeEvent, RuntimeQuery, RuntimeStore
from demiurge.util import utc_id


ActionKind = Literal[
    "agent.turn",
    "agent.spawn",
    "tool.call",
    "authored_tool.call",
    "mcp.call",
    "terminal.exec",
    "evolver.run",
    "schedule.fire",
    "delivery.send",
    "approval.request",
    "state.patch",
    "artifact.write",
]

TaskCommand = Literal["cancel"]


@dataclass(frozen=True, slots=True)
class ActionSpec:
    kind: ActionKind
    payload: dict[str, Any] = field(default_factory=dict)
    idempotency_key: str | None = None


@dataclass(frozen=True, slots=True)
class ActionSource:
    actor: str
    session_id: str | None = None
    turn_id: str | None = None
    core_id: str | None = None
    task_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TaskHandle:
    task_id: str
    kind: str
    status: str


@dataclass(frozen=True, slots=True)
class ActionResult:
    task_id: str | None
    status: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TaskFilter:
    status: str | None = None
    kind: str | None = None
    owner_session_id: str | None = None
    parent_task_id: str | None = None
    limit: int = 50


@dataclass(frozen=True, slots=True)
class EventCursor:
    seq: int = 0


@dataclass(frozen=True, slots=True)
class EventFilter:
    aggregate_type: str | None = None
    aggregate_id: str | None = None
    event_type: str | None = None
    limit: int = 100


@dataclass(frozen=True, slots=True)
class EventBatch:
    events: tuple[dict[str, Any], ...]
    next_cursor: EventCursor


TaskRecord = dict[str, Any]
TaskView = dict[str, Any]


class RuntimeControlPlane:
    """Host-owned action and task control plane."""

    def __init__(self, store: RuntimeStore):
        self.store = store

    def submit(self, spec: ActionSpec, *, source: ActionSource) -> TaskHandle | ActionResult:
        task_id = spec.payload.get("task_id") or utc_id("task_")
        root_task_id = spec.payload.get("root_task_id") or source.task_id or task_id
        event = RuntimeEvent(
            type="task.submitted",
            aggregate_type="task",
            aggregate_id=task_id,
            actor=self._source_actor(source),
            payload={
                "kind": spec.kind,
                "status": "queued",
                "root_task_id": root_task_id,
                "parent_task_id": spec.payload.get("parent_task_id") or source.task_id,
                "owner_session_id": spec.payload.get("owner_session_id") or source.session_id,
                "owner_turn_id": spec.payload.get("owner_turn_id") or source.turn_id,
                "core_id": spec.payload.get("core_id") or source.core_id,
                "source": self._source_actor(source),
                "notify_policy": spec.payload.get("notify_policy"),
                "action": spec.payload,
            },
        )
        result = self.store.append([event], idempotency_key=spec.idempotency_key)
        row = result.events[-1]
        return TaskHandle(task_id=str(row["aggregate_id"]), kind=spec.kind, status="queued")

    def control(self, task_id: str, command: TaskCommand) -> TaskRecord:
        if command != "cancel":
            raise ValueError(f"unsupported task control command: {command}")
        return self.cancel(task_id)

    def mark_started(self, task_id: str, *, source: ActionSource | None = None) -> TaskRecord:
        self._append_task_event(
            "task.started",
            task_id,
            payload={"status": "running"},
            source=source,
        )
        return self.read(task_id, view="operator")

    def succeed(
        self,
        task_id: str,
        *,
        result_ref: str | None = None,
        summary: str | None = None,
        metadata: dict[str, Any] | None = None,
        source: ActionSource | None = None,
    ) -> TaskRecord:
        payload: dict[str, Any] = {"status": "succeeded"}
        if result_ref is not None:
            payload["result_ref"] = result_ref
        if summary is not None:
            payload["summary"] = summary
        if metadata:
            payload["metadata"] = dict(metadata)
        self._append_task_event("task.succeeded", task_id, payload=payload, source=source)
        return self.read(task_id, view="operator")

    def fail(
        self,
        task_id: str,
        *,
        error: str,
        summary: str | None = None,
        metadata: dict[str, Any] | None = None,
        source: ActionSource | None = None,
    ) -> TaskRecord:
        payload: dict[str, Any] = {
            "status": "failed",
            "summary": summary if summary is not None else error,
            "error": {"message": error},
        }
        if metadata:
            payload["metadata"] = dict(metadata)
        self._append_task_event("task.failed", task_id, payload=payload, source=source)
        return self.read(task_id, view="operator")

    def cancel(
        self,
        task_id: str,
        *,
        summary: str = "task cancelled",
        metadata: dict[str, Any] | None = None,
        source: ActionSource | None = None,
    ) -> TaskRecord:
        payload: dict[str, Any] = {
            "status": "cancelled",
            "summary": summary,
            "error": {"message": summary},
        }
        if metadata:
            payload["metadata"] = dict(metadata)
        self._append_task_event("task.cancelled", task_id, payload=payload, source=source)
        return self.read(task_id, view="operator")

    def block(
        self,
        task_id: str,
        *,
        summary: str,
        metadata: dict[str, Any] | None = None,
        source: ActionSource | None = None,
    ) -> TaskRecord:
        payload: dict[str, Any] = {"status": "blocked_needs_user", "summary": summary}
        if metadata:
            payload["metadata"] = dict(metadata)
        self._append_task_event("task.blocked", task_id, payload=payload, source=source)
        return self.read(task_id, view="operator")

    def append_task_log(
        self,
        task_id: str,
        text: str,
        *,
        stream: str = "stdout",
        source: ActionSource | None = None,
    ) -> None:
        lines = str(text).splitlines() or [str(text)]
        self.store.append(
            [
                RuntimeEvent(
                    type="task.log",
                    aggregate_type="task",
                    aggregate_id=task_id,
                    actor=self._source_actor(source) if source is not None else None,
                    payload={"stream": stream, "text": line},
                )
                for line in lines
            ]
        )

    def record_events(self, events: list[RuntimeEvent]) -> None:
        """Append host runtime events through the control-plane seam."""
        if not events:
            return
        self.store.append(events)

    def emit_completion_ready(
        self,
        *,
        event_id: str,
        task_id: str,
        payload: dict[str, Any],
        source: ActionSource | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        result = self.store.append(
            [
                RuntimeEvent(
                    type="task.completion_ready",
                    aggregate_type="task_completion",
                    aggregate_id=event_id,
                    actor=self._source_actor(source) if source is not None else None,
                    payload={"task_id": task_id, **payload},
                ),
                durable_work_enqueued_event(
                    DurableWorkSpec(
                        work_id=event_id,
                        kind="task.completion",
                        owner_session_id=payload.get("owner_session_id"),
                        owner_turn_id=payload.get("owner_turn_id"),
                        parent_work_id=task_id,
                        payload={"task_id": task_id, **payload},
                    ),
                    actor=self._source_actor(source) if source is not None else None,
                ),
            ],
            idempotency_key=idempotency_key,
        )
        return dict(result.events[0])

    def ack_completion(self, event_id: str) -> None:
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

    def query(self, filter: TaskFilter) -> list[TaskRecord]:
        where: dict[str, Any] = {}
        if filter.status:
            where["status"] = filter.status
        if filter.kind:
            where["kind"] = filter.kind
        if filter.owner_session_id:
            where["owner_session_id"] = filter.owner_session_id
        if filter.parent_task_id:
            where["parent_task_id"] = filter.parent_task_id
        page = self.store.query(RuntimeQuery(table="tasks", where=where, order_by="created_at", limit=filter.limit))
        return list(page.rows)

    def read(self, task_id: str, view: Literal["model", "operator", "debug"] = "operator") -> TaskView:
        tasks = self.store.query(RuntimeQuery(table="tasks", where={"task_id": task_id}, limit=1)).rows
        if not tasks:
            raise KeyError(f"task not found: {task_id}")
        record = dict(tasks[0])
        if view in {"operator", "debug"}:
            logs = self.store.query(RuntimeQuery(table="task_logs", where={"task_id": task_id}, order_by="seq")).rows
            record["logs"] = list(logs)
        if view == "debug":
            events = self.store.query(
                RuntimeQuery(
                    table="runtime_events",
                    where={"aggregate_type": "task", "aggregate_id": task_id},
                    order_by="seq",
                )
            ).rows
            record["events"] = list(events)
        return record

    def stream(self, cursor: EventCursor, filter: EventFilter) -> EventBatch:
        where: dict[str, Any] = {}
        if filter.aggregate_type:
            where["aggregate_type"] = filter.aggregate_type
        if filter.aggregate_id:
            where["aggregate_id"] = filter.aggregate_id
        if filter.event_type:
            where["type"] = filter.event_type
        page = self.store.query_events_after(cursor.seq, where=where, limit=filter.limit)
        events = tuple(page.rows)
        next_seq = max((int(event["seq"]) for event in events), default=cursor.seq)
        return EventBatch(events=events, next_cursor=EventCursor(seq=next_seq))

    def _append_task_event(
        self,
        event_type: str,
        task_id: str,
        *,
        payload: dict[str, Any],
        source: ActionSource | None,
    ) -> None:
        self.store.append(
            [
                RuntimeEvent(
                    type=event_type,
                    aggregate_type="task",
                    aggregate_id=task_id,
                    actor=self._source_actor(source) if source is not None else None,
                    payload=payload,
                )
            ]
        )

    def _source_actor(self, source: ActionSource) -> dict[str, Any]:
        return {
            "actor": source.actor,
            "session_id": source.session_id,
            "turn_id": source.turn_id,
            "core_id": source.core_id,
            "task_id": source.task_id,
            "metadata": dict(source.metadata),
        }
