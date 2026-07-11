from __future__ import annotations

import hashlib
import json
import os
import random
import sqlite3
import time
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Literal, TypeVar

from demiurge.runtime.conversation_keys import build_conversation_key
from demiurge.runtime.scope import (
    AuthorityKind,
    PrincipalScope,
    PrincipalScopeResolver,
    _active_operator_authority,
    _scope_from_record,
)
from demiurge.util import ensure_dir, utc_id
from demiurge.storage import utc_now

if os.name == "nt":
    import msvcrt
else:
    import fcntl


SCHEMA_VERSION = 5


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
        "runtime_work_items",
        "session_bindings",
        "session_owners",
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
        self._principal_scope_issuer = object()
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
        def write(connection: sqlite3.Connection) -> AppendResult:
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
            return AppendResult(events=tuple(rows), first_seq=rows[0]["seq"], last_seq=rows[-1]["seq"])

        return self._write(write)

    def query(self, query: RuntimeQuery) -> ProjectionPage:
        return self._query(query)

    def query_owned(self, scope: PrincipalScope, query: RuntimeQuery) -> ProjectionPage:
        PrincipalScopeResolver(self).validate_owned(scope)
        owner_field = {
            "messages": "session_id",
            "sessions": "session_id",
            "tasks": "owner_session_id",
        }.get(query.table)
        if owner_field is None:
            raise ValueError(f"runtime owner predicate is not defined for table: {query.table}")
        if scope.authority is AuthorityKind.OPERATOR:
            return self._query(
                query,
                owner_field=owner_field,
                require_durable_owner=True,
            )
        return self._query(
            query,
            owner_field=owner_field,
            allowed_owner_ids=scope.allowed_session_ids,
        )

    def session_owner_exists(self, session_id: str) -> bool:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT 1 FROM session_owners WHERE session_id = ? LIMIT 1",
                (session_id,),
            ).fetchone()
        return row is not None

    def session_owner_count(self) -> int:
        with self._connection() as connection:
            row = connection.execute("SELECT COUNT(*) FROM session_owners").fetchone()
        return int(row[0]) if row is not None else 0

    def has_operator_scope_audit(
        self,
        *,
        active_session_id: str,
        principal_id: str,
    ) -> bool:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT 1
                FROM runtime_events
                WHERE aggregate_type = 'principal_scope'
                  AND aggregate_id = ?
                  AND type = 'principal_scope.operator_issued'
                  AND json_extract(payload_json, '$.active_session_id') = ?
                ORDER BY seq DESC
                LIMIT 1
                """,
                (principal_id, active_session_id),
            ).fetchone()
        return row is not None

    def _query(
        self,
        query: RuntimeQuery,
        *,
        owner_field: str | None = None,
        allowed_owner_ids: frozenset[str] | None = None,
        require_durable_owner: bool = False,
    ) -> ProjectionPage:
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
        if owner_field is not None:
            if require_durable_owner:
                clauses.append(
                    "EXISTS (SELECT 1 FROM session_owners AS scope_owner "
                    f"WHERE scope_owner.session_id = {query.table}.{owner_field})"
                )
            else:
                owner_ids = sorted(allowed_owner_ids or ())
                if not owner_ids:
                    return ProjectionPage(rows=(), limit=query.limit, offset=query.offset)
                placeholders = ", ".join("?" for _ in owner_ids)
                clauses.append(f"{owner_field} IN ({placeholders})")
                values.extend(owner_ids)
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

    def query_events_after(
        self,
        after_seq: int,
        *,
        where: dict[str, Any] | None = None,
        limit: int = 100,
    ) -> ProjectionPage:
        filters = dict(where or {})
        clauses = ["seq > ?"]
        values: list[Any] = [int(after_seq)]
        for key, value in filters.items():
            if not key.replace("_", "").isalnum():
                raise ValueError(f"invalid runtime query field: {key}")
            clauses.append(f"{key} = ?")
            values.append(value)
        sql = "SELECT * FROM runtime_events WHERE " + " AND ".join(clauses) + " ORDER BY seq LIMIT ?"
        values.append(limit)
        with self._connection() as connection:
            rows = [self._decode_row(dict(row)) for row in connection.execute(sql, values).fetchall()]
        return ProjectionPage(rows=tuple(rows), limit=limit, offset=0)

    def transaction(self, fn: Callable[[sqlite3.Connection], T]) -> T:
        return self._write(fn)

    def _initialize(self) -> None:
        with self._connection() as connection:
            current_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if current_version == 4:
                with _runtime_migration_lock(self.path):
                    current_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                    if current_version == 4:
                        backup_path = self.path.with_name(
                            f"{self.path.name}.v{current_version}.bak"
                        )
                        try:
                            self._backup_before_migration(connection, current_version)
                            self._migrate_v4_to_v5(connection, backup_path=backup_path)
                        except Exception as exc:
                            detail = str(exc)
                            if "backup" in detail and (
                                "invalid" in detail or "does not match" in detail
                            ):
                                recovery = (
                                    "move the invalid or stale backup aside, then retry so the Host "
                                    "can create a backup of the unchanged original database"
                                )
                            else:
                                recovery = (
                                    "restore by stopping Demiurge and replacing the original database "
                                    "with that backup before retrying the upgrade"
                                )
                            raise RuntimeError(
                                f"runtime database migration failed: {detail}; "
                                f"original database remains unchanged at {self.path}; "
                                f"pre-migration backup path: {backup_path.resolve()}; "
                                f"{recovery}"
                            ) from exc
                        current_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if current_version not in {0, SCHEMA_VERSION}:
                raise RuntimeError(
                    f"unsupported runtime database schema version {current_version}; "
                    f"expected 4 or {SCHEMA_VERSION}."
                )
            connection.executescript(_SCHEMA_SQL)
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            connection.commit()

    def _backup_before_migration(self, connection: sqlite3.Connection, version: int) -> None:
        backup_path = self.path.with_name(f"{self.path.name}.v{version}.bak")
        if backup_path.exists():
            self._validate_migration_backup(
                backup_path,
                version,
                source_connection=connection,
            )
            return
        temporary_path = backup_path.with_name(f".{backup_path.name}.{os.getpid()}.tmp")
        try:
            temporary_path.unlink(missing_ok=True)
            descriptor = os.open(
                temporary_path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
            os.close(descriptor)
            with sqlite3.connect(temporary_path) as backup:
                connection.backup(backup)
                backup.commit()
            if os.name != "nt":
                temporary_path.chmod(0o600)
            os.replace(temporary_path, backup_path)
            self._validate_migration_backup(
                backup_path,
                version,
                source_connection=connection,
            )
        finally:
            temporary_path.unlink(missing_ok=True)

    @staticmethod
    def _validate_migration_backup(
        backup_path: Path,
        version: int,
        *,
        source_connection: sqlite3.Connection | None = None,
    ) -> None:
        try:
            with sqlite3.connect(f"{backup_path.resolve().as_uri()}?mode=ro", uri=True) as backup:
                backup_version = int(backup.execute("PRAGMA user_version").fetchone()[0])
                integrity = str(backup.execute("PRAGMA integrity_check").fetchone()[0])
        except sqlite3.DatabaseError as exc:
            raise RuntimeError(f"runtime migration backup is invalid: {backup_path}") from exc
        if backup_version != version or integrity.lower() != "ok":
            raise RuntimeError(
                f"runtime migration backup is invalid: {backup_path} "
                f"(version={backup_version}, integrity={integrity})"
            )
        if source_connection is not None:
            with sqlite3.connect(f"{backup_path.resolve().as_uri()}?mode=ro", uri=True) as backup:
                if RuntimeStore._database_fingerprint(backup) != RuntimeStore._database_fingerprint(
                    source_connection
                ):
                    raise RuntimeError(
                        "runtime migration backup does not match the current database: "
                        f"{backup_path}"
                    )

    @staticmethod
    def _database_fingerprint(connection: sqlite3.Connection) -> str:
        digest = hashlib.sha256()
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        digest.update(f"user_version={version}\n".encode())
        for statement in connection.iterdump():
            digest.update(statement.encode("utf-8"))
            digest.update(b"\n")
        return digest.hexdigest()

    def _migrate_v4_to_v5(
        self,
        connection: sqlite3.Connection,
        *,
        backup_path: Path,
    ) -> None:
        try:
            connection.execute("BEGIN IMMEDIATE")
            current_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if current_version == SCHEMA_VERSION:
                connection.rollback()
                return
            if current_version != 4:
                raise RuntimeError(
                    f"runtime schema changed during migration: expected 4, found {current_version}"
                )
            self._validate_migration_backup(
                backup_path,
                current_version,
                source_connection=connection,
            )
            _ensure_session_owner_schema(connection)
            rows = connection.execute(
                """
                SELECT session_id, channel, target_json, created_at, updated_at
                FROM sessions
                """
            ).fetchall()
            for row in rows:
                session_id = str(row[0])
                channel = str(row[1]) if row[1] is not None else None
                try:
                    target = json.loads(str(row[2]) or "{}")
                except (TypeError, ValueError):
                    target = {}
                if not isinstance(target, dict):
                    target = {}
                metadata = target.get("metadata") if isinstance(target.get("metadata"), dict) else {}
                conversation_key = _optional_text(target.get("conversation_key"))
                source = _optional_text(metadata.get("source"))
                owner_kind, principal_id, owner_channel, owner_conversation = self._legacy_session_owner(
                    connection,
                    session_id=session_id,
                    channel=channel,
                    conversation_key=conversation_key,
                    source=source,
                )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO session_owners (
                        session_id, owner_kind, principal_id, channel,
                        conversation_key, origin_session_id, origin_turn_id,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, NULL, NULL, ?, ?)
                    """,
                    (
                        session_id,
                        owner_kind,
                        principal_id,
                        owner_channel,
                        owner_conversation,
                        str(row[3]),
                        str(row[4]),
                    ),
                )
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
            if integrity.lower() != "ok":
                raise RuntimeError(f"runtime database integrity check failed after migration: {integrity}")
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    @staticmethod
    def _legacy_session_owner(
        connection: sqlite3.Connection,
        *,
        session_id: str,
        channel: str | None,
        conversation_key: str | None,
        source: str | None,
    ) -> tuple[str, str, str | None, str | None]:
        if channel == "tui":
            return "operator", build_conversation_key("principal", "operator", "local"), None, None
        bindings = connection.execute(
            """
            SELECT channel, conversation_key
            FROM session_bindings
            WHERE session_id = ?
            """,
            (session_id,),
        ).fetchall()
        if (
            channel
            and conversation_key
            and source
            and channel != "webhook"
            and len(bindings) == 1
            and str(bindings[0][0]) == channel
            and str(bindings[0][1]) == conversation_key
        ):
            principal_id = build_conversation_key(
                "principal",
                "conversation",
                channel,
                conversation_key,
            )
            return "conversation", principal_id, channel, conversation_key
        return (
            "legacy_local",
            build_conversation_key("principal", "legacy_local", session_id),
            None,
            None,
        )

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        with closing(self._connect()) as connection:
            with connection:
                yield connection

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=0.1)
        try:
            connection.row_factory = sqlite3.Row
            attempts = 6
            for attempt in range(attempts):
                try:
                    connection.execute("PRAGMA journal_mode=WAL")
                    break
                except sqlite3.OperationalError as exc:
                    if not _is_sqlite_busy(exc) or attempt == attempts - 1:
                        raise
                    time.sleep(0.025 * (2**attempt) + random.uniform(0, 0.01))
            connection.execute("PRAGMA foreign_keys=ON")
            return connection
        except Exception:
            connection.close()
            raise

    def _write(self, fn: Callable[[sqlite3.Connection], T]) -> T:
        attempts = 6
        for attempt in range(attempts):
            with self._connection() as connection:
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    result = fn(connection)
                    connection.commit()
                    return result
                except sqlite3.OperationalError as exc:
                    connection.rollback()
                    if not _is_sqlite_busy(exc) or attempt == attempts - 1:
                        raise
            time.sleep(0.025 * (2**attempt) + random.uniform(0, 0.01))
        raise RuntimeError("sqlite write retry loop exhausted")

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
                    delivery_id, owner_turn_id, channel, target_json, status, idempotency_key,
                    payload_json, attempts, last_error, created_at, sent_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event["aggregate_id"],
                    payload.get("owner_turn_id"),
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
        elif event_type in {
            "delivery.sent",
            "delivery.failed",
            "delivery.retry_scheduled",
            "delivery.sending",
            "delivery.unknown",
            "delivery.unrouted",
        }:
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
        elif event_type == "work.enqueued":
            self._project_work_enqueued(connection, event)
        elif event_type in {
            "work.claimed",
            "work.running",
            "work.sending",
            "work.heartbeat",
            "work.succeeded",
            "work.failed",
            "work.cancelled",
            "work.unknown",
            "work.acknowledged",
            "work.corrupt",
        }:
            self._project_work_updated(connection, event)
        elif event_type == "session.binding_conflict":
            connection.execute(
                """
                DELETE FROM sessions
                WHERE session_id = ?
                  AND NOT EXISTS (SELECT 1 FROM messages WHERE messages.session_id = sessions.session_id)
                """,
                (payload.get("loser_session_id") or event["aggregate_id"],),
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
            self._project_session_binding(connection, event)
            self._project_session_owner(connection, event)
        elif event_type == "session.binding.rebound":
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
            self._project_session_binding(connection, event, replace=True)
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
            self._project_session_binding(connection, event)
        elif event_type == "turn.started":
            connection.execute(
                """
                INSERT OR REPLACE INTO turns (
                    turn_id, session_id, status, input_ref, result_ref, created_at, completed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event["aggregate_id"],
                    payload.get("session_id"),
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
                    artifact_id, owner_turn_id, kind, uri, metadata_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    event["aggregate_id"],
                    payload.get("owner_turn_id"),
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
                    call_id, turn_id, step_id, tool_name, status, args_json, result_json, created_at, completed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event["aggregate_id"],
                    payload.get("turn_id"),
                    payload.get("step_id"),
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
                    core_id, schedule_id, due_at, task_id, claim_status, idempotency_key,
                    claim_id, lease_expires_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload.get("core_id"),
                    payload.get("schedule_id"),
                    payload.get("due_at"),
                    payload.get("task_id"),
                    payload.get("claim_status", "claimed"),
                    payload.get("idempotency_key"),
                    payload.get("claim_id"),
                    payload.get("lease_expires_at"),
                ),
            )
        elif event_type in {"scheduler.completed", "scheduler.error"}:
            connection.execute(
                """
                UPDATE scheduler_instances
                SET task_id = COALESCE(?, task_id),
                    claim_status = ?,
                    claim_id = COALESCE(?, claim_id),
                    lease_expires_at = COALESCE(?, lease_expires_at)
                WHERE core_id = ? AND schedule_id = ? AND due_at = ?
                """,
                (
                    payload.get("task_id"),
                    payload.get("claim_status") or ("error" if event_type == "scheduler.error" else "completed"),
                    payload.get("claim_id"),
                    payload.get("lease_expires_at"),
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
                try:
                    row[target] = json.loads(value) if value else None
                except json.JSONDecodeError:
                    row[target] = {"_corrupt_json": value}
        return row

    def _project_work_enqueued(self, connection: sqlite3.Connection, event: dict[str, Any]) -> None:
        payload = event["payload"]
        now = event["created_at"]
        connection.execute(
            """
            INSERT OR REPLACE INTO runtime_work_items (
                work_id, kind, status, owner_session_id, owner_turn_id, parent_work_id,
                claim_id, owner_id, claimed_at, lease_expires_at, attempts,
                next_attempt_at, last_error, external_ref, payload_json,
                created_at, updated_at, completed_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event["aggregate_id"],
                payload.get("kind"),
                payload.get("status", "queued"),
                payload.get("owner_session_id"),
                payload.get("owner_turn_id"),
                payload.get("parent_work_id"),
                payload.get("claim_id"),
                payload.get("owner_id"),
                payload.get("claimed_at"),
                payload.get("lease_expires_at"),
                int(payload.get("attempts") or 0),
                payload.get("next_attempt_at"),
                payload.get("last_error"),
                payload.get("external_ref"),
                json.dumps(payload.get("payload") or {}, ensure_ascii=False, sort_keys=True),
                payload.get("created_at") or now,
                now,
                payload.get("completed_at"),
            ),
        )

    def _project_work_updated(self, connection: sqlite3.Connection, event: dict[str, Any]) -> None:
        payload = event["payload"]
        event_type = event["type"]
        terminal_update = event_type in {
            "work.succeeded",
            "work.cancelled",
            "work.acknowledged",
            "work.corrupt",
        } or (event_type == "work.failed" and payload.get("status") == "failed")
        completed_at = event["created_at"] if terminal_update else None
        payload_json = None
        if "payload" in payload:
            payload_json = json.dumps(payload.get("payload") or {}, ensure_ascii=False, sort_keys=True)
        connection.execute(
            """
            UPDATE runtime_work_items
            SET status = COALESCE(?, status),
                claim_id = COALESCE(?, claim_id),
                owner_id = COALESCE(?, owner_id),
                claimed_at = COALESCE(?, claimed_at),
                lease_expires_at = COALESCE(?, lease_expires_at),
                attempts = COALESCE(?, attempts),
                next_attempt_at = ?,
                last_error = COALESCE(?, last_error),
                external_ref = COALESCE(?, external_ref),
                payload_json = COALESCE(?, payload_json),
                updated_at = ?,
                completed_at = COALESCE(?, completed_at)
            WHERE work_id = ?
            """,
            (
                payload.get("status"),
                payload.get("claim_id"),
                payload.get("owner_id"),
                payload.get("claimed_at"),
                payload.get("lease_expires_at"),
                payload.get("attempts"),
                payload.get("next_attempt_at"),
                payload.get("last_error"),
                payload.get("external_ref"),
                payload_json,
                event["created_at"],
                completed_at,
                event["aggregate_id"],
            ),
        )

    def _project_session_binding(
        self,
        connection: sqlite3.Connection,
        event: dict[str, Any],
        *,
        replace: bool = False,
    ) -> None:
        payload = event["payload"]
        target = payload.get("target") if isinstance(payload.get("target"), dict) else {}
        core_id = payload.get("core_id")
        channel = payload.get("channel")
        conversation_key = target.get("conversation_key")
        if not core_id or not channel or not conversation_key:
            return
        if replace:
            cursor = connection.execute(
                """
                UPDATE session_bindings
                SET session_id = ?, updated_at = ?
                WHERE core_id = ? AND channel = ? AND conversation_key = ?
                """,
                (event["aggregate_id"], event["created_at"], core_id, channel, conversation_key),
            )
            if cursor.rowcount:
                return
        connection.execute(
            """
            INSERT OR IGNORE INTO session_bindings (
                core_id, channel, conversation_key, session_id, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                core_id,
                channel,
                conversation_key,
                event["aggregate_id"],
                event["created_at"],
                event["created_at"],
            ),
        )
        connection.execute(
            """
            UPDATE session_bindings
            SET updated_at = ?
            WHERE core_id = ? AND channel = ? AND conversation_key = ? AND session_id = ?
            """,
            (event["created_at"], core_id, channel, conversation_key, event["aggregate_id"]),
        )

    def _project_session_owner(self, connection: sqlite3.Connection, event: dict[str, Any]) -> None:
        payload = event["payload"]
        target = payload.get("target") if isinstance(payload.get("target"), dict) else {}
        scope_record = target.get("principal_scope")
        if isinstance(scope_record, dict):
            scope = _scope_from_record(
                scope_record,
                issuer=self._principal_scope_issuer,
                operator_authority=_active_operator_authority(self),
            )
            if scope.session_id != event["aggregate_id"]:
                raise ValueError("persisted PrincipalScope session does not match session event")
            owner_kind = scope.authority.value
            principal_id = scope.principal_id
            channel = scope.channel
            conversation_key = scope.conversation_key
            origin_session_id = scope.origin_session_id
            origin_turn_id = scope.origin_turn_id
        else:
            owner_kind = "legacy_local"
            principal_id = build_conversation_key("principal", "legacy_local", event["aggregate_id"])
            channel = None
            conversation_key = None
            origin_session_id = None
            origin_turn_id = None
        connection.execute(
            """
            INSERT OR IGNORE INTO session_owners (
                session_id, owner_kind, principal_id, channel,
                conversation_key, origin_session_id, origin_turn_id,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event["aggregate_id"],
                owner_kind,
                principal_id,
                channel,
                conversation_key,
                origin_session_id,
                origin_turn_id,
                payload.get("created_at") or event["created_at"],
                payload.get("updated_at") or event["created_at"],
            ),
        )


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
    "runtime_work_items",
    "session_bindings",
    "session_owners",
}
_QUERY_ORDER_FIELDS = {
    "seq",
    "runtime_seq",
    "created_at",
    "updated_at",
    "started_at",
    "completed_at",
    "due_at",
    "lease_expires_at",
    "next_attempt_at",
}


def _is_sqlite_busy(exc: sqlite3.OperationalError) -> bool:
    message = str(exc).lower()
    return "database is locked" in message or "database is busy" in message


_SESSION_OWNER_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS session_owners (
        session_id TEXT PRIMARY KEY,
        owner_kind TEXT NOT NULL,
        principal_id TEXT NOT NULL,
        channel TEXT,
        conversation_key TEXT,
        origin_session_id TEXT,
        origin_turn_id TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_session_owners_principal
    ON session_owners (principal_id, updated_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_session_owners_route
    ON session_owners (channel, conversation_key)
    """,
)
_SESSION_OWNER_SCHEMA_SQL = ";\n".join(_SESSION_OWNER_SCHEMA_STATEMENTS) + ";\n"

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
    owner_turn_id TEXT,
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
    turn_id TEXT,
    step_id TEXT,
    tool_name TEXT NOT NULL,
    status TEXT NOT NULL,
    args_json TEXT NOT NULL DEFAULT '{}',
    result_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id TEXT PRIMARY KEY,
    owner_turn_id TEXT,
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
    claim_id TEXT,
    lease_expires_at TEXT,
    PRIMARY KEY (core_id, schedule_id, due_at)
);

CREATE TABLE IF NOT EXISTS runtime_work_items (
    work_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    owner_session_id TEXT,
    owner_turn_id TEXT,
    parent_work_id TEXT,
    claim_id TEXT,
    owner_id TEXT,
    claimed_at TEXT,
    lease_expires_at TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT,
    last_error TEXT,
    external_ref TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_runtime_work_due ON runtime_work_items (kind, status, next_attempt_at, lease_expires_at);
CREATE INDEX IF NOT EXISTS idx_runtime_work_owner_session ON runtime_work_items (owner_session_id, created_at);

CREATE TABLE IF NOT EXISTS session_bindings (
    core_id TEXT NOT NULL,
    channel TEXT NOT NULL,
    conversation_key TEXT NOT NULL,
    session_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (core_id, channel, conversation_key)
);
CREATE INDEX IF NOT EXISTS idx_session_bindings_session ON session_bindings (session_id);

""" + _SESSION_OWNER_SCHEMA_SQL


def _ensure_session_owner_schema(connection: sqlite3.Connection) -> None:
    for statement in _SESSION_OWNER_SCHEMA_STATEMENTS:
        connection.execute(statement)


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


@contextmanager
def _runtime_migration_lock(database_path: Path) -> Iterator[None]:
    lock_path = database_path.with_name(f"{database_path.name}.migrate.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(descriptor, "a+b") as lock_file:
        if os.name != "nt":
            lock_path.chmod(0o600)
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        else:
            lock_file.seek(0, os.SEEK_END)
            if lock_file.tell() == 0:
                lock_file.write(b"0")
                lock_file.flush()
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
        try:
            yield
        finally:
            if os.name != "nt":
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            else:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
