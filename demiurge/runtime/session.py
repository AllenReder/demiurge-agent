from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from demiurge.runtime.control import RuntimeControlPlane
from demiurge.runtime.store import RuntimeEvent, RuntimeQuery
from demiurge.storage import SessionMessage, SessionRecord, SessionStore


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
    """Host-owned session admission and projection module."""

    def __init__(self, *, session_store: SessionStore, control_plane: RuntimeControlPlane | None = None):
        self.session_store = session_store
        self.control_plane = control_plane

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
        record = self.session_store.get(query.session_id)
        session = asdict(record)
        if self.control_plane is None:
            messages = tuple(asdict(message) for message in self.session_store.read_messages(query.session_id))
            return SessionSnapshot(session=session, messages=messages if query.include_messages else ())
        store = self.control_plane.store
        messages: tuple[dict[str, Any], ...] = ()
        turns: tuple[dict[str, Any], ...] = ()
        if query.include_messages:
            messages = store.query(
                RuntimeQuery(table="messages", where={"session_id": query.session_id}, order_by="created_at", limit=1000)
            ).rows
        if query.include_turns:
            turns = store.query(
                RuntimeQuery(table="turns", where={"session_id": query.session_id}, order_by="created_at", limit=1000)
            ).rows
        return SessionSnapshot(session=session, messages=messages, turns=turns)

    def ensure_session(self, session_id: str, **kwargs: Any) -> tuple[SessionRecord, bool]:
        record, created = self.session_store.ensure_session(session_id, **kwargs)
        self._project_session(record, "session.created" if created else "session.resumed")
        return record, created

    def create_session(self, **kwargs: Any) -> SessionRecord:
        record = self.session_store.create_session(**kwargs)
        self._project_session(record, "session.created")
        return record

    def update_session(self, session_id: str, **kwargs: Any) -> SessionRecord:
        record = self.session_store.update_session(session_id, **kwargs)
        self._project_session(record, "session.updated")
        return record

    def append_message(self, session_id: str, **kwargs: Any) -> SessionMessage:
        message = self.session_store.append_message(session_id, **kwargs)
        self._project_message(message)
        self._project_session(self.session_store.get(session_id), "session.updated")
        return message

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
        message = self.session_store.append_message(
            session_id,
            role="system",
            content=content,
            turn_id=turn_id,
            kind="compaction_summary",
            visible=False,
            model_visible=True,
            metadata={"compacted_count": compacted_count, "focus": focus},
        )
        self._project_message(message)
        record = self.session_store.update_session(
            session_id,
            compaction_summary_id=message.id,
            compacted_until_message_id=compacted_until_message_id,
        )
        self._project_session(record, "session.updated")
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

    def _project_session(self, record: SessionRecord, event_type: str) -> None:
        self._append_runtime_event(
            RuntimeEvent(
                type=event_type,
                aggregate_type="session",
                aggregate_id=record.session_id,
                payload={
                    "core_id": record.core_id,
                    "core_version": record.core_version,
                    "status": "active",
                    "channel": record.channel,
                    "target": {
                        "conversation_key": record.conversation_key,
                        "workspace": record.workspace,
                        "provider": record.provider,
                        "model": record.model,
                        "title": record.title,
                        "preview": record.preview,
                        "message_count": record.message_count,
                        "metadata": record.metadata or {},
                    },
                    "created_at": record.created_at,
                    "updated_at": record.updated_at,
                },
            )
        )

    def _project_message(self, message: SessionMessage) -> None:
        self._append_runtime_event(
            RuntimeEvent(
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
        )

    def _append_runtime_event(self, event: RuntimeEvent) -> None:
        if self.control_plane is None:
            return
        self.control_plane.store.append([event])
