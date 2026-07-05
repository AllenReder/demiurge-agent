from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from demiurge.runtime.durable_work import DurableWorkSpec, durable_work_enqueued_event
from demiurge.runtime.control import RuntimeControlPlane
from demiurge.runtime.store import RuntimeEvent, RuntimeQuery
from demiurge.storage import SessionMessage, SessionRecord, utc_now
from demiurge.util import utc_id


SessionCommandKind = Literal[
    "ensure",
    "create",
    "update",
    "start_turn",
    "complete_turn",
    "append_message",
]


@dataclass(frozen=True, slots=True)
class SessionCommand:
    kind: SessionCommandKind
    session_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SessionReceipt:
    session_id: str
    status: str
    turn_id: str | None = None
    message_id: str | None = None
    created: bool | None = None
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SessionQuery:
    session_id: str
    include_messages: bool = False
    include_turns: bool = False


@dataclass(frozen=True, slots=True)
class SessionSnapshot:
    session: dict[str, Any]
    messages: tuple[dict[str, Any], ...] = ()
    turns: tuple[dict[str, Any], ...] = ()


class SessionRuntime:
    """SQLite-backed host-owned session runtime.

    SessionRuntime is the only hot-path owner for session, turn, message,
    bootstrap, and compaction state. It deliberately does not read or mirror
    the historical ``~/.demiurge/sessions`` JSON layout.
    """

    def __init__(self, *, control_plane: RuntimeControlPlane):
        self.control_plane = control_plane

    @property
    def store(self):
        return self.control_plane.store

    def command(self, command: SessionCommand) -> SessionReceipt:
        payload = dict(command.payload)
        if command.kind == "ensure":
            record, created = self.ensure_session(session_id=str(command.session_id or ""), **payload)
            return SessionReceipt(record.session_id, "created" if created else "resumed", created=created, data=asdict(record))
        if command.kind == "create":
            record = self.create_session(session_id=command.session_id, **payload)
            return SessionReceipt(record.session_id, "created", created=True, data=asdict(record))
        if command.kind == "update":
            if not command.session_id:
                raise ValueError("session update requires session_id")
            record = self.update_session(command.session_id, **payload)
            return SessionReceipt(record.session_id, "updated", data=asdict(record))
        if command.kind == "start_turn":
            turn_id = str(payload.pop("turn_id"))
            session_id = str(command.session_id or payload.get("session_id") or "")
            payload.pop("session_id", None)
            self.start_turn(session_id=session_id, turn_id=turn_id, **payload)
            return SessionReceipt(session_id, "running", turn_id=turn_id)
        if command.kind == "complete_turn":
            turn_id = str(payload.pop("turn_id"))
            session_id = str(command.session_id or payload.get("session_id") or "")
            status = str(payload.get("status") or "completed")
            payload.pop("session_id", None)
            self.complete_turn(session_id=session_id, turn_id=turn_id, **payload)
            return SessionReceipt(session_id, status, turn_id=turn_id)
        if command.kind == "append_message":
            if not command.session_id:
                raise ValueError("append_message requires session_id")
            message = self.append_message(command.session_id, **payload)
            return SessionReceipt(command.session_id, "message_persisted", turn_id=message.turn_id, message_id=message.id)
        raise ValueError(f"unsupported session command: {command.kind}")

    def read(self, query: SessionQuery) -> SessionSnapshot:
        session = asdict(self.get_session(query.session_id))
        messages: tuple[dict[str, Any], ...] = ()
        turns: tuple[dict[str, Any], ...] = ()
        if query.include_messages:
            messages = self.store.query(
                RuntimeQuery(table="messages", where={"session_id": query.session_id}, order_by="runtime_seq", limit=1000)
            ).rows
        if query.include_turns:
            turns = self.store.query(
                RuntimeQuery(table="turns", where={"session_id": query.session_id}, order_by="created_at", limit=1000)
            ).rows
        return SessionSnapshot(session=session, messages=messages, turns=turns)

    def exists(self, session_id: str) -> bool:
        return bool(self.store.query(RuntimeQuery(table="sessions", where={"session_id": session_id}, limit=1)).rows)

    def ensure_session(self, session_id: str, **kwargs: Any) -> tuple[SessionRecord, bool]:
        existing = self._resolve_binding_from_kwargs(kwargs)
        if existing is not None:
            record = self.update_session(existing, touch=False, **kwargs)
            self._append_runtime_event(self._session_event(record, "session.resumed"))
            return record, False
        if self.exists(session_id):
            record = self.update_session(session_id, touch=False, **kwargs)
            self._append_runtime_event(self._session_event(record, "session.resumed"))
            return record, False
        return self.create_session(session_id=session_id, **kwargs), True

    def create_session(
        self,
        *,
        session_id: str | None = None,
        core_id: str,
        core_revision: str,
        channel: str | None = None,
        conversation_key: str | None = None,
        workspace: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SessionRecord:
        now = utc_now()
        record = SessionRecord(
            session_id=session_id or utc_id("session_"),
            core_id=core_id,
            core_revision=core_revision,
            created_at=now,
            updated_at=now,
            channel=channel,
            conversation_key=conversation_key,
            workspace=workspace,
            provider=provider,
            model=model,
            metadata=metadata or {},
        )
        try:
            self._append_runtime_event(self._session_event(record, "session.created"))
        except RuntimeError:
            existing = self.resolve_interaction_session(
                core_id=core_id,
                channel=channel,
                conversation_key=conversation_key,
            )
            if existing is not None:
                return self.get_session(existing)
            raise
        existing = self.resolve_interaction_session(
            core_id=core_id,
            channel=channel,
            conversation_key=conversation_key,
        )
        if existing is not None and existing != record.session_id:
            self._append_runtime_event(
                RuntimeEvent(
                    type="session.binding_conflict",
                    aggregate_type="session",
                    aggregate_id=record.session_id,
                    payload={
                        "core_id": core_id,
                        "channel": channel,
                        "conversation_key": conversation_key,
                        "winner_session_id": existing,
                        "loser_session_id": record.session_id,
                    },
                )
            )
            return self.get_session(existing)
        return record

    def update_session(
        self,
        session_id: str,
        *,
        core_id: str | None = None,
        core_revision: str | None = None,
        channel: str | None = None,
        conversation_key: str | None = None,
        workspace: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        title: str | None = None,
        preview: str | None = None,
        message_count: int | None = None,
        compaction_summary_id: str | None = None,
        compacted_until_message_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        bootstrap_context: str | None = None,
        bootstrap_context_initialized: bool | None = None,
        touch: bool = True,
    ) -> SessionRecord:
        current = self.get_session(session_id)
        merged_metadata = dict(current.metadata or {})
        if metadata:
            merged_metadata.update(metadata)
        record = SessionRecord(
            session_id=session_id,
            core_id=core_id if core_id is not None else current.core_id,
            core_revision=core_revision if core_revision is not None else current.core_revision,
            created_at=current.created_at,
            updated_at=utc_now() if touch else current.updated_at,
            channel=channel if channel is not None else current.channel,
            conversation_key=conversation_key if conversation_key is not None else current.conversation_key,
            workspace=workspace if workspace is not None else current.workspace,
            provider=provider if provider is not None else current.provider,
            model=model if model is not None else current.model,
            title=title if title is not None else current.title,
            preview=preview if preview is not None else current.preview,
            message_count=message_count if message_count is not None else current.message_count,
            compaction_summary_id=(
                compaction_summary_id if compaction_summary_id is not None else current.compaction_summary_id
            ),
            compacted_until_message_id=(
                compacted_until_message_id
                if compacted_until_message_id is not None
                else current.compacted_until_message_id
            ),
            metadata=merged_metadata,
        )
        event = self._session_event(record, "session.updated")
        if bootstrap_context is not None or bootstrap_context_initialized is not None:
            payload = dict(event.payload)
            target = dict(payload["target"])
            if bootstrap_context is not None:
                target["bootstrap_context"] = bootstrap_context
            if bootstrap_context_initialized is not None:
                target["bootstrap_context_initialized"] = bootstrap_context_initialized
            payload["target"] = target
            event = RuntimeEvent(
                type=event.type,
                aggregate_type=event.aggregate_type,
                aggregate_id=event.aggregate_id,
                payload=payload,
            )
        self._append_runtime_event(event)
        return self.get_session(session_id)

    def rebind_interaction_session(
        self,
        session_id: str,
        *,
        core_id: str,
        core_revision: str,
        channel: str,
        conversation_key: str,
        metadata: dict[str, Any] | None = None,
    ) -> SessionRecord:
        if not channel or not conversation_key:
            raise ValueError("interaction session rebind requires channel and conversation_key")
        current = self.get_session(session_id)
        merged_metadata = dict(current.metadata or {})
        if metadata:
            merged_metadata.update(metadata)
        record = SessionRecord(
            session_id=session_id,
            core_id=core_id,
            core_revision=core_revision,
            created_at=current.created_at,
            updated_at=utc_now(),
            channel=channel,
            conversation_key=conversation_key,
            workspace=current.workspace,
            provider=current.provider,
            model=current.model,
            title=current.title,
            preview=current.preview,
            message_count=current.message_count,
            compaction_summary_id=current.compaction_summary_id,
            compacted_until_message_id=current.compacted_until_message_id,
            metadata=merged_metadata,
        )
        self._append_runtime_event(self._session_event(record, "session.binding.rebound"))
        return self.get_session(session_id)

    def get_session(self, session_id: str) -> SessionRecord:
        rows = self.store.query(RuntimeQuery(table="sessions", where={"session_id": session_id}, limit=1)).rows
        if not rows:
            raise FileNotFoundError(f"session not found: {session_id}")
        return self._record_from_row(rows[0])

    def list_sessions(self, *, core_id: str | None = None, limit: int = 20) -> list[SessionRecord]:
        rows = self.store.query(RuntimeQuery(table="sessions", order_by="updated_at", limit=max(limit * 5, limit))).rows
        records = [self._record_from_row(row) for row in rows]
        if core_id:
            records = [record for record in records if record.core_id == core_id]
        records.sort(key=lambda item: item.updated_at, reverse=True)
        return records[:limit]

    def resolve_interaction_session(self, *, core_id: str, channel: str | None, conversation_key: str | None) -> str | None:
        if not channel or not conversation_key:
            return None
        rows = self.store.query(
            RuntimeQuery(
                table="session_bindings",
                where={"core_id": core_id, "channel": channel, "conversation_key": conversation_key},
                limit=1,
            )
        ).rows
        if rows:
            return str(rows[0]["session_id"])
        return None

    def can_bind_session(self, session_id: str, *, channel: str | None, conversation_key: str | None) -> bool:
        if not self.exists(session_id):
            return True
        record = self.get_session(session_id)
        if record.message_count == 0:
            return True
        return record.channel == channel and record.conversation_key == conversation_key

    def append_message(
        self,
        session_id: str,
        *,
        role: str,
        content: str,
        turn_id: str | None = None,
        kind: str = "message",
        visible: bool = True,
        model_visible: bool = True,
        interaction_metadata: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SessionMessage:
        message = self._build_message(
            session_id,
            role=role,
            content=content,
            turn_id=turn_id,
            kind=kind,
            visible=visible,
            model_visible=model_visible,
            interaction_metadata=interaction_metadata,
            metadata=metadata,
        )
        self._append_runtime_event(self._message_event(message))
        self._update_after_message(session_id, message)
        return message

    def append_delivery_message(
        self,
        session_id: str,
        *,
        role: str,
        content: str,
        delivery_id: str,
        task_id: str | None,
        channel: str | None,
        target: dict[str, Any],
        delivery_payload: dict[str, Any],
        delivery_status: str = "queued",
        delivery_idempotency_key: str | None = None,
        turn_id: str | None = None,
        kind: str = "message",
        visible: bool = True,
        model_visible: bool = True,
        interaction_metadata: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SessionMessage:
        message_metadata = dict(metadata or {})
        message_metadata["message_id"] = None
        message = self._build_message(
            session_id,
            role=role,
            content=content,
            turn_id=turn_id,
            kind=kind,
            visible=visible,
            model_visible=model_visible,
            interaction_metadata=interaction_metadata,
            metadata=message_metadata,
        )
        assert message.metadata is not None
        message.metadata["message_id"] = message.id
        payload = dict(delivery_payload)
        payload["message_id"] = message.id
        result = self.store.append(
            [
                self._message_event(message),
                RuntimeEvent(
                    type="delivery.queued",
                    aggregate_type="delivery",
                    aggregate_id=delivery_id,
                    payload={
                        "task_id": task_id,
                        "channel": channel,
                        "target": dict(target),
                        "status": delivery_status,
                        "idempotency_key": delivery_idempotency_key or delivery_id,
                        "payload": payload,
                    },
                ),
                durable_work_enqueued_event(
                    DurableWorkSpec(
                        work_id=delivery_id,
                        kind="delivery.send",
                        owner_session_id=session_id,
                        owner_turn_id=turn_id,
                        parent_work_id=task_id,
                        payload={
                            "task_id": task_id,
                            "channel": channel,
                            "target": dict(target),
                            "idempotency_key": delivery_idempotency_key or delivery_id,
                            **payload,
                        },
                    )
                ),
            ],
            idempotency_key=f"delivery:{delivery_id}:message_outbox",
        )
        persisted_message_id = next(
            (str(event["aggregate_id"]) for event in result.events if event.get("type") == "message.persisted"),
            message.id,
        )
        if persisted_message_id != message.id:
            rows = self.store.query(RuntimeQuery(table="messages", where={"message_id": persisted_message_id}, limit=1)).rows
            if not rows:
                raise FileNotFoundError(f"message not found after delivery append replay: {persisted_message_id}")
            return self._message_from_row(rows[0])
        self._update_after_message(session_id, message)
        return message

    def _build_message(
        self,
        session_id: str,
        *,
        role: str,
        content: str,
        turn_id: str | None = None,
        kind: str = "message",
        visible: bool = True,
        model_visible: bool = True,
        interaction_metadata: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SessionMessage:
        if not self.exists(session_id):
            raise FileNotFoundError(f"session not found: {session_id}")
        interaction_metadata = interaction_metadata or {}
        return SessionMessage(
            id=utc_id("msg_"),
            session_id=session_id,
            turn_id=turn_id,
            role=role,
            content=content,
            created_at=utc_now(),
            kind=kind,
            visible=visible,
            model_visible=model_visible,
            channel=interaction_metadata.get("channel"),
            source=interaction_metadata.get("source"),
            reply_to=interaction_metadata.get("reply_to"),
            conversation_key=interaction_metadata.get("conversation_key"),
            metadata=metadata or {},
        )

    def read_messages(self, session_id: str) -> list[SessionMessage]:
        rows = self.store.query(
            RuntimeQuery(table="messages", where={"session_id": session_id}, order_by="runtime_seq", limit=10_000)
        ).rows
        return [self._message_from_row(row) for row in rows]

    def message_count(self, session_id: str) -> int:
        return len(self.read_messages(session_id))

    def latest_turn_id(self, session_id: str) -> str | None:
        for message in reversed(self.read_messages(session_id)):
            if message.turn_id:
                return message.turn_id
        return None

    def history_for_context(self, session_id: str) -> list[SessionMessage]:
        messages = self.read_messages(session_id)
        cutoff_index = self._compacted_until_index(session_id, messages)
        result: list[SessionMessage] = []
        for index, message in enumerate(messages):
            if index <= cutoff_index:
                continue
            if message.kind != "message" or not message.model_visible:
                continue
            if message.role not in {"user", "assistant", "system", "tool"}:
                continue
            result.append(message)
        return result

    def write_bootstrap_context(self, session_id: str, content: str) -> None:
        self.update_session(session_id, bootstrap_context=content, bootstrap_context_initialized=True)

    def read_bootstrap_context(self, session_id: str) -> str:
        row = self._session_row(session_id)
        target = row.get("target") or {}
        return str(target.get("bootstrap_context") or "")

    def bootstrap_context_exists(self, session_id: str) -> bool:
        row = self._session_row(session_id)
        target = row.get("target") or {}
        return bool(target.get("bootstrap_context_initialized"))

    def latest_compaction_summary(self, session_id: str) -> SessionMessage | None:
        for message in reversed(self.read_messages(session_id)):
            if message.kind == "compaction_summary" and message.model_visible:
                return message
        return None

    def write_compaction_summary(
        self,
        session_id: str,
        *,
        content: str,
        turn_id: str,
        compacted_until_message_id: str,
        compacted_count: int,
        focus: str | None = None,
    ) -> SessionMessage:
        message = self.append_message(
            session_id,
            role="system",
            content=content,
            turn_id=turn_id,
            kind="compaction_summary",
            visible=False,
            model_visible=True,
            metadata={"compacted_count": compacted_count, "focus": focus},
        )
        self.update_session(
            session_id,
            compaction_summary_id=message.id,
            compacted_until_message_id=compacted_until_message_id,
        )
        return message

    def start_turn(
        self,
        *,
        session_id: str,
        turn_id: str,
        task_id: str | None = None,
        input_ref: str | None = None,
    ) -> None:
        self._append_runtime_event(
            RuntimeEvent(
                type="turn.started",
                aggregate_type="turn",
                aggregate_id=turn_id,
                payload={
                    "session_id": session_id,
                    "task_id": task_id,
                    "status": "running",
                    "input_ref": input_ref,
                },
            )
        )

    def complete_turn(
        self,
        *,
        session_id: str,
        turn_id: str,
        status: str = "completed",
        result_ref: str | None = None,
    ) -> None:
        event_type = "turn.completed" if status == "completed" else f"turn.{status}"
        self._append_runtime_event(
            RuntimeEvent(
                type=event_type,
                aggregate_type="turn",
                aggregate_id=turn_id,
                payload={"session_id": session_id, "status": status, "result_ref": result_ref},
            )
        )

    def _update_after_message(self, session_id: str, message: SessionMessage) -> None:
        record = self.get_session(session_id)
        preview = record.preview
        title = record.title
        if message.kind == "message" and message.visible and message.content.strip():
            preview = message.content.strip().replace("\n", " ")[:120]
            if title is None and message.role == "user":
                title = preview[:80]
        self.update_session(
            session_id,
            title=title,
            preview=preview,
            message_count=self.message_count(session_id),
            touch=True,
        )

    def _session_event(self, record: SessionRecord, event_type: str) -> RuntimeEvent:
        target = {
            "conversation_key": record.conversation_key,
            "workspace": record.workspace,
            "provider": record.provider,
            "model": record.model,
            "title": record.title,
            "preview": record.preview,
            "message_count": record.message_count,
            "compaction_summary_id": record.compaction_summary_id,
            "compacted_until_message_id": record.compacted_until_message_id,
            "metadata": record.metadata or {},
            "core_revision": record.core_revision,
        }
        with_existing = self.store.query(
            RuntimeQuery(table="sessions", where={"session_id": record.session_id}, limit=1)
        ).rows
        if with_existing:
            existing_target = with_existing[0].get("target") or {}
            for key in ("bootstrap_context", "bootstrap_context_initialized"):
                if key in existing_target and key not in target:
                    target[key] = existing_target[key]
        return RuntimeEvent(
            type=event_type,
            aggregate_type="session",
            aggregate_id=record.session_id,
            payload={
                "core_id": record.core_id,
                "core_revision": record.core_revision,
                "status": "active",
                "channel": record.channel,
                "target": target,
                "created_at": record.created_at,
                "updated_at": record.updated_at,
            },
        )

    def _message_event(self, message: SessionMessage) -> RuntimeEvent:
        return RuntimeEvent(
            type="message.persisted",
            aggregate_type="message",
            aggregate_id=message.id,
            payload={
                "session_id": message.session_id,
                "turn_id": message.turn_id,
                "role": message.role,
                "visibility": "visible" if message.visible else "hidden",
                "visible": message.visible,
                "created_at": message.created_at,
                "content": {
                    "text": message.content,
                    "kind": message.kind,
                    "model_visible": message.model_visible,
                    "channel": message.channel,
                    "source": message.source,
                    "reply_to": message.reply_to,
                    "conversation_key": message.conversation_key,
                    "metadata": message.metadata or {},
                },
            },
        )

    def _session_row(self, session_id: str) -> dict[str, Any]:
        rows = self.store.query(RuntimeQuery(table="sessions", where={"session_id": session_id}, limit=1)).rows
        if not rows:
            raise FileNotFoundError(f"session not found: {session_id}")
        return dict(rows[0])

    def _record_from_row(self, row: dict[str, Any]) -> SessionRecord:
        target = row.get("target") or {}
        metadata = target.get("metadata") if isinstance(target.get("metadata"), dict) else {}
        return SessionRecord(
            session_id=str(row["session_id"]),
            core_id=str(row.get("core_id") or ""),
            core_revision=str(target.get("core_revision") or ""),
            created_at=str(row.get("created_at") or ""),
            updated_at=str(row.get("updated_at") or ""),
            channel=row.get("channel"),
            conversation_key=target.get("conversation_key"),
            workspace=target.get("workspace"),
            provider=target.get("provider"),
            model=target.get("model"),
            title=target.get("title"),
            preview=target.get("preview"),
            message_count=int(target.get("message_count") or 0),
            compaction_summary_id=target.get("compaction_summary_id"),
            compacted_until_message_id=target.get("compacted_until_message_id"),
            metadata=metadata,
        )

    def _message_from_row(self, row: dict[str, Any]) -> SessionMessage:
        content = row.get("content") or {}
        metadata = content.get("metadata") if isinstance(content.get("metadata"), dict) else {}
        return SessionMessage(
            id=str(row["message_id"]),
            session_id=str(row["session_id"]),
            turn_id=row.get("turn_id"),
            role=str(row.get("role") or "assistant"),
            content=str(content.get("text") or ""),
            created_at=str(row.get("created_at") or ""),
            kind=str(content.get("kind") or "message"),
            visible=str(row.get("visibility") or "visible") == "visible",
            model_visible=bool(content.get("model_visible", True)),
            channel=content.get("channel"),
            source=content.get("source"),
            reply_to=content.get("reply_to"),
            conversation_key=content.get("conversation_key"),
            metadata=metadata,
        )

    def _resolve_binding_from_kwargs(self, values: dict[str, Any]) -> str | None:
        core_id = values.get("core_id")
        channel = values.get("channel")
        conversation_key = values.get("conversation_key")
        if not core_id or not channel or not conversation_key:
            return None
        return self.resolve_interaction_session(
            core_id=str(core_id),
            channel=str(channel),
            conversation_key=str(conversation_key),
        )

    def _compacted_until_index(self, session_id: str, messages: list[SessionMessage]) -> int:
        if not self.exists(session_id):
            return -1
        marker = self.get_session(session_id).compacted_until_message_id
        if not marker:
            return -1
        for index, message in enumerate(messages):
            if message.id == marker:
                return index
        return -1

    def _append_runtime_event(self, event: RuntimeEvent) -> None:
        self.store.append([event])
