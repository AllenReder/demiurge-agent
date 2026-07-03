from __future__ import annotations

import json
import sqlite3
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Literal, TypeVar

from demiurge.util import ensure_dir, utc_id
from demiurge.storage import utc_now


SCHEMA_VERSION = 2


@dataclass(frozen=True, slots=True)
class RuntimeEvent:
    type: str
    aggregate_type: str
    aggregate_id: str
    payload: dict[str, Any]
    actor: dict[str, Any] | None = None
    event_id: str | None = None
    created_at: str | None = None


@dataclass(frozen=True, slots=True)
class AppendResult:
    events: tuple[dict[str, Any], ...]
    first_seq: int | None
    last_seq: int | None


@dataclass(frozen=True, slots=True)
class RuntimeQuery:
    table: Literal[
        "runtime_events",
        "tasks",
        "task_dependencies",
        "task_logs",
        "leases",
        "approvals",
        "outbox",
        "sessions",
        "turns",
        "messages",
        "tool_calls",
        "artifacts",
        "scheduler_instances",
    ] = "runtime_events"
    where: dict[str, Any] | None = None
    order_by: str | None = None
    limit: int = 100
    offset: int = 0


@dataclass(frozen=True, slots=True)
class ProjectionPage:
    rows: tuple[dict[str, Any], ...]
    limit: int
    offset: int


T = TypeVar("T")


class RuntimeStore:
    """SQLite-backed runtime event store and projection surface."""

    def __init__(self, path: Path):
        self.path = path.expanduser().resolve()
        ensure_dir(self.path.parent)
        self._initialize()

    @classmethod
    def default(cls, home: Path) -> "RuntimeStore":
        return cls(home / "runtime" / "runtime.sqlite3")

    def append(
        self,
        events: Iterable[RuntimeEvent],
        *,
        idempotency_key: str | None = None,
        expected: dict[str, Any] | None = None,
    ) -> AppendResult:
        materialized = list(events)
        if not materialized:
            return AppendResult(events=(), first_seq=None, last_seq=None)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if idempotency_key:
                existing = self._events_by_idempotency_key(connection, idempotency_key)
                if existing:
                    return AppendResult(
                        events=tuple(existing),
                        first_seq=int(existing[0]["seq"]),
                        last_seq=int(existing[-1]["seq"]),
                    )
            self._check_expected(connection, expected)
            rows: list[dict[str, Any]] = []
            for event in materialized:
                event_id = event.event_id or utc_id("evt_")
                created_at = event.created_at or utc_now()
                cursor = connection.execute(
                    """
                    INSERT INTO runtime_events (
                        event_id, aggregate_type, aggregate_id, type, created_at,
                        actor_json, payload_json, idempotency_key
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_id,
                        event.aggregate_type,
                        event.aggregate_id,
                        event.type,
                        created_at,
                        json.dumps(event.actor or {}, ensure_ascii=False, sort_keys=True),
                        json.dumps(event.payload, ensure_ascii=False, sort_keys=True),
                        idempotency_key,
                    ),
                )
                seq = int(cursor.lastrowid)
                row = {
                    "seq": seq,
                    "event_id": event_id,
                    "aggregate_type": event.aggregate_type,
                    "aggregate_id": event.aggregate_id,
                    "type": event.type,
                    "created_at": created_at,
                    "actor": event.actor or {},
                    "payload": event.payload,
                    "idempotency_key": idempotency_key,
                }
                rows.append(row)
                self._apply_projection(connection, row)
            connection.commit()
            return AppendResult(events=tuple(rows), first_seq=rows[0]["seq"], last_seq=rows[-1]["seq"])

    def query(self, query: RuntimeQuery) -> ProjectionPage:
        if query.table not in _QUERY_TABLES:
            raise ValueError(f"unsupported runtime query table: {query.table}")
        where = query.where or {}
        clauses: list[str] = []
        values: list[Any] = []
        for key, value in where.items():
            if not key.replace("_", "").isalnum():
                raise ValueError(f"invalid runtime query field: {key}")
            clauses.append(f"{key} = ?")
            values.append(value)
        sql = f"SELECT * FROM {query.table}"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        if query.order_by:
            if query.order_by not in _QUERY_ORDER_FIELDS:
                raise ValueError(f"invalid runtime query order_by: {query.order_by}")
            sql += f" ORDER BY {query.order_by}"
        sql += " LIMIT ? OFFSET ?"
        values.extend([query.limit, query.offset])
        with self._connection() as connection:
            rows = [self._decode_row(dict(row)) for row in connection.execute(sql, values).fetchall()]
        return ProjectionPage(rows=tuple(rows), limit=query.limit, offset=query.offset)

    def transaction(self, fn: Callable[[sqlite3.Connection], T]) -> T:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                result = fn(connection)
            except Exception:
                connection.rollback()
                raise
            connection.commit()
            return result

    def _initialize(self) -> None:
        with self._connection() as connection:
            current_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if current_version not in {0, SCHEMA_VERSION}:
                raise RuntimeError(
                    f"unsupported runtime database schema version {current_version}; "
                    f"expected {SCHEMA_VERSION}. Demiurge does not migrate old runtime state."
                )
            connection.executescript(_SCHEMA_SQL)
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            connection.commit()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        with closing(self._connect()) as connection:
            with connection:
                yield connection

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _events_by_idempotency_key(self, connection: sqlite3.Connection, key: str) -> list[dict[str, Any]]:
        rows = connection.execute(
            "SELECT * FROM runtime_events WHERE idempotency_key = ? ORDER BY seq",
            (key,),
        ).fetchall()
        return [self._decode_row(dict(row)) for row in rows]

    def _check_expected(self, connection: sqlite3.Connection, expected: dict[str, Any] | None) -> None:
        if not expected:
            return
        aggregate_type = expected.get("aggregate_type")
        aggregate_id = expected.get("aggregate_id")
        seq = expected.get("last_seq")
        if aggregate_type is None or aggregate_id is None or seq is None:
            raise ValueError("expected must include aggregate_type, aggregate_id, and last_seq")
        current = connection.execute(
            """
            SELECT seq FROM runtime_events
            WHERE aggregate_type = ? AND aggregate_id = ?
            ORDER BY seq DESC LIMIT 1
            """,
            (aggregate_type, aggregate_id),
        ).fetchone()
        current_seq = int(current["seq"]) if current is not None else None
        if current_seq != seq:
            raise RuntimeError(f"runtime aggregate changed: expected {seq}, got {current_seq}")

    def _apply_projection(self, connection: sqlite3.Connection, event: dict[str, Any]) -> None:
        payload = event["payload"]
        event_type = event["type"]
        if event_type == "task.submitted":
            connection.execute(
                """
                INSERT OR REPLACE INTO tasks (
                    task_id, kind, status, root_task_id, parent_task_id, owner_session_id,
                    owner_turn_id, core_id, source_json, notify_policy, result_ref,
                    error_json, created_at, started_at, completed_at, heartbeat_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event["aggregate_id"],
                    payload.get("kind"),
                    payload.get("status", "queued"),
                    payload.get("root_task_id") or event["aggregate_id"],
                    payload.get("parent_task_id"),
                    payload.get("owner_session_id"),
                    payload.get("owner_turn_id"),
                    payload.get("core_id"),
                    json.dumps(payload.get("source") or {}, ensure_ascii=False, sort_keys=True),
                    payload.get("notify_policy"),
                    payload.get("result_ref"),
                    json.dumps(payload.get("error") or {}, ensure_ascii=False, sort_keys=True),
                    event["created_at"],
                    None,
                    None,
                    None,
                ),
            )
        elif event_type in {"task.started", "task.succeeded", "task.failed", "task.cancelled", "task.lost", "task.blocked"}:
            status = payload.get("status") or event_type.split(".", 1)[1]
            started_at = event["created_at"] if event_type == "task.started" else None
            completed_at = event["created_at"] if event_type in {"task.succeeded", "task.failed", "task.cancelled", "task.lost"} else None
            connection.execute(
                """
                UPDATE tasks
                SET status = ?,
                    started_at = COALESCE(?, started_at),
                    completed_at = COALESCE(?, completed_at),
                    result_ref = COALESCE(?, result_ref),
                    error_json = COALESCE(?, error_json),
                    heartbeat_at = COALESCE(?, heartbeat_at)
                WHERE task_id = ?
                """,
                (
                    status,
                    started_at,
                    completed_at,
                    payload.get("result_ref"),
                    json.dumps(payload.get("error"), ensure_ascii=False, sort_keys=True)
                    if payload.get("error") is not None
                    else None,
                    event["created_at"],
                    event["aggregate_id"],
                ),
            )
        elif event_type == "task.log":
            connection.execute(
                "INSERT INTO task_logs (task_id, stream, text, created_at) VALUES (?, ?, ?, ?)",
                (event["aggregate_id"], payload.get("stream", "stdout"), payload.get("text", ""), event["created_at"]),
            )
        elif event_type == "delivery.queued":
            connection.execute(
                """
                INSERT OR REPLACE INTO outbox (
                    delivery_id, task_id, channel, target_json, status, idempotency_key,
                    payload_json, attempts, last_error, created_at, sent_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event["aggregate_id"],
                    payload.get("task_id"),
                    payload.get("channel"),
                    json.dumps(payload.get("target") or {}, ensure_ascii=False, sort_keys=True),
                    payload.get("status", "queued"),
                    payload.get("idempotency_key"),
                    json.dumps(payload.get("payload") or {}, ensure_ascii=False, sort_keys=True),
                    int(payload.get("attempts") or 0),
                    payload.get("last_error"),
                    event["created_at"],
                    None,
                ),
            )
        elif event_type in {"delivery.sent", "delivery.failed", "delivery.retry_scheduled"}:
            status = payload.get("status") or event_type.split(".", 1)[1]
            connection.execute(
                """
                UPDATE outbox
                SET status = ?,
                    attempts = COALESCE(?, attempts),
                    last_error = ?,
                    sent_at = COALESCE(?, sent_at)
                WHERE delivery_id = ?
                """,
                (
                    status,
                    payload.get("attempts"),
                    payload.get("last_error"),
                    payload.get("sent_at") or (event["created_at"] if event_type == "delivery.sent" else None),
                    event["aggregate_id"],
                ),
            )
        elif event_type == "session.created":
            connection.execute(
                """
                INSERT OR REPLACE INTO sessions (
                    session_id, core_id, status, channel, target_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event["aggregate_id"],
                    payload.get("core_id"),
                    payload.get("status", "active"),
                    payload.get("channel"),
                    json.dumps(payload.get("target") or {}, ensure_ascii=False, sort_keys=True),
                    payload.get("created_at") or event["created_at"],
                    payload.get("updated_at") or event["created_at"],
                ),
            )
        elif event_type in {"session.updated", "session.resumed"}:
            connection.execute(
                """
                UPDATE sessions
                SET core_id = COALESCE(?, core_id),
                    status = COALESCE(?, status),
                    channel = COALESCE(?, channel),
                    target_json = COALESCE(?, target_json),
                    updated_at = COALESCE(?, updated_at)
                WHERE session_id = ?
                """,
                (
                    payload.get("core_id"),
                    payload.get("status"),
                    payload.get("channel"),
                    json.dumps(payload.get("target"), ensure_ascii=False, sort_keys=True)
                    if payload.get("target") is not None
                    else None,
                    payload.get("updated_at") or event["created_at"],
                    event["aggregate_id"],
                ),
            )
        elif event_type == "turn.started":
            connection.execute(
                """
                INSERT OR REPLACE INTO turns (
                    turn_id, session_id, task_id, status, input_ref, result_ref, created_at, completed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event["aggregate_id"],
                    payload.get("session_id"),
                    payload.get("task_id"),
                    payload.get("status", "running"),
                    payload.get("input_ref"),
                    payload.get("result_ref"),
                    payload.get("created_at") or event["created_at"],
                    None,
                ),
            )
        elif event_type in {"turn.completed", "turn.failed", "turn.cancelled"}:
            connection.execute(
                """
                UPDATE turns
                SET status = ?,
                    result_ref = COALESCE(?, result_ref),
                    completed_at = COALESCE(?, completed_at)
                WHERE turn_id = ?
                """,
                (
                    payload.get("status") or event_type.split(".", 1)[1],
                    payload.get("result_ref"),
                    payload.get("completed_at") or event["created_at"],
                    event["aggregate_id"],
                ),
            )
        elif event_type == "message.persisted":
            content = payload.get("content")
            if not isinstance(content, dict):
                content = {"text": payload.get("text") or "", "metadata": payload.get("metadata") or {}}
            connection.execute(
                """
                INSERT OR REPLACE INTO messages (
                    message_id, session_id, turn_id, role, visibility, content_json, created_at, runtime_seq
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event["aggregate_id"],
                    payload.get("session_id"),
                    payload.get("turn_id"),
                    payload.get("role"),
                    payload.get("visibility") or ("visible" if payload.get("visible", True) else "hidden"),
                    json.dumps(content, ensure_ascii=False, sort_keys=True),
                    payload.get("created_at") or event["created_at"],
                    event["seq"],
                ),
            )
        elif event_type == "message.updated":
            content = payload.get("content")
            if not isinstance(content, dict):
                content = {"text": payload.get("text") or "", "metadata": payload.get("metadata") or {}}
            connection.execute(
                """
                UPDATE messages
                SET content_json = ?
                WHERE message_id = ?
                """,
                (
                    json.dumps(content, ensure_ascii=False, sort_keys=True),
                    event["aggregate_id"],
                ),
            )
        elif event_type == "artifact.stored":
            connection.execute(
                """
                INSERT OR REPLACE INTO artifacts (
                    artifact_id, task_id, kind, uri, metadata_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    event["aggregate_id"],
                    payload.get("task_id"),
                    payload.get("kind") or "file",
                    payload.get("uri") or "",
                    json.dumps(payload.get("metadata") or {}, ensure_ascii=False, sort_keys=True),
                    payload.get("created_at") or event["created_at"],
                ),
            )
        elif event_type == "tool.call.started":
            connection.execute(
                """
                INSERT OR REPLACE INTO tool_calls (
                    call_id, task_id, turn_id, tool_name, status, args_json, result_json, created_at, completed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event["aggregate_id"],
                    payload.get("task_id"),
                    payload.get("turn_id"),
                    payload.get("tool_name"),
                    payload.get("status", "running"),
                    json.dumps(payload.get("args") or {}, ensure_ascii=False, sort_keys=True),
                    json.dumps({}, ensure_ascii=False, sort_keys=True),
                    payload.get("created_at") or event["created_at"],
                    None,
                ),
            )
        elif event_type in {"tool.call.completed", "tool.call.failed"}:
            connection.execute(
                """
                UPDATE tool_calls
                SET status = ?,
                    result_json = ?,
                    completed_at = ?
                WHERE call_id = ?
                """,
                (
                    payload.get("status") or ("failed" if event_type == "tool.call.failed" else "succeeded"),
                    json.dumps(payload.get("result") or {}, ensure_ascii=False, sort_keys=True),
                    payload.get("completed_at") or event["created_at"],
                    event["aggregate_id"],
                ),
            )
        elif event_type in {"scheduler.scheduled", "scheduler.claimed"}:
            connection.execute(
                """
                INSERT OR REPLACE INTO scheduler_instances (
                    core_id, schedule_id, due_at, task_id, claim_status, idempotency_key
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    payload.get("core_id"),
                    payload.get("schedule_id"),
                    payload.get("due_at"),
                    payload.get("task_id"),
                    payload.get("claim_status", "claimed"),
                    payload.get("idempotency_key"),
                ),
            )
        elif event_type in {"scheduler.completed", "scheduler.error"}:
            connection.execute(
                """
                UPDATE scheduler_instances
                SET task_id = COALESCE(?, task_id),
                    claim_status = ?
                WHERE core_id = ? AND schedule_id = ? AND due_at = ?
                """,
                (
                    payload.get("task_id"),
                    payload.get("claim_status") or ("error" if event_type == "scheduler.error" else "completed"),
                    payload.get("core_id"),
                    payload.get("schedule_id"),
                    payload.get("due_at"),
                ),
            )

    def _decode_row(self, row: dict[str, Any]) -> dict[str, Any]:
        for key in list(row):
            if key.endswith("_json"):
                target = key.removesuffix("_json")
                value = row.pop(key)
                row[target] = json.loads(value) if value else None
        return row


_QUERY_TABLES = {
    "runtime_events",
    "tasks",
    "task_dependencies",
    "task_logs",
    "leases",
    "approvals",
    "outbox",
    "sessions",
    "turns",
    "messages",
    "tool_calls",
    "artifacts",
    "scheduler_instances",
}
_QUERY_ORDER_FIELDS = {"seq", "runtime_seq", "created_at", "updated_at", "started_at", "completed_at", "due_at"}

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS runtime_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    aggregate_type TEXT NOT NULL,
    aggregate_id TEXT NOT NULL,
    type TEXT NOT NULL,
    created_at TEXT NOT NULL,
    actor_json TEXT NOT NULL DEFAULT '{}',
    payload_json TEXT NOT NULL DEFAULT '{}',
    idempotency_key TEXT
);
CREATE INDEX IF NOT EXISTS idx_runtime_events_aggregate ON runtime_events (aggregate_type, aggregate_id, seq);
CREATE INDEX IF NOT EXISTS idx_runtime_events_idempotency ON runtime_events (idempotency_key) WHERE idempotency_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    root_task_id TEXT,
    parent_task_id TEXT,
    owner_session_id TEXT,
    owner_turn_id TEXT,
    core_id TEXT,
    source_json TEXT NOT NULL DEFAULT '{}',
    notify_policy TEXT,
    result_ref TEXT,
    error_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    heartbeat_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_tasks_owner_session ON tasks (owner_session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_tasks_parent ON tasks (parent_task_id, created_at);

CREATE TABLE IF NOT EXISTS task_logs (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    stream TEXT NOT NULL,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_task_logs_task ON task_logs (task_id, seq);

CREATE TABLE IF NOT EXISTS task_dependencies (
    parent_task_id TEXT NOT NULL,
    child_task_id TEXT NOT NULL,
    dependency_policy TEXT NOT NULL,
    PRIMARY KEY (parent_task_id, child_task_id)
);

CREATE TABLE IF NOT EXISTS leases (
    resource_key TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    worker_id TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS approvals (
    approval_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    capability TEXT NOT NULL,
    risk TEXT,
    status TEXT NOT NULL,
    request_json TEXT NOT NULL DEFAULT '{}',
    decision_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS outbox (
    delivery_id TEXT PRIMARY KEY,
    task_id TEXT,
    channel TEXT,
    target_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL,
    idempotency_key TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at TEXT NOT NULL,
    sent_at TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    core_id TEXT NOT NULL,
    status TEXT NOT NULL,
    channel TEXT,
    target_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS turns (
    turn_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    task_id TEXT,
    status TEXT NOT NULL,
    input_ref TEXT,
    result_ref TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS messages (
    message_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    turn_id TEXT,
    role TEXT NOT NULL,
    visibility TEXT NOT NULL,
    content_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    runtime_seq INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages (session_id, runtime_seq);

CREATE TABLE IF NOT EXISTS tool_calls (
    call_id TEXT PRIMARY KEY,
    task_id TEXT,
    turn_id TEXT,
    tool_name TEXT NOT NULL,
    status TEXT NOT NULL,
    args_json TEXT NOT NULL DEFAULT '{}',
    result_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id TEXT PRIMARY KEY,
    task_id TEXT,
    kind TEXT NOT NULL,
    uri TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scheduler_instances (
    core_id TEXT NOT NULL,
    schedule_id TEXT NOT NULL,
    due_at TEXT NOT NULL,
    task_id TEXT,
    claim_status TEXT NOT NULL,
    idempotency_key TEXT,
    PRIMARY KEY (core_id, schedule_id, due_at)
);
"""
