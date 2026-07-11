from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol

from demiurge.runtime.text_format import format_table
from demiurge.storage import SessionRecord


class SessionListRuntime(Protocol):
    def list_sessions(self, *, core_id: str | None = None, limit: int = 20) -> list[SessionRecord]:
        ...


class SessionRouteBindingRuntime(Protocol):
    def bind(self, router: Any, session_id: str) -> Any:
        ...


@dataclass(frozen=True, slots=True)
class SessionChoice:
    index: int
    record: SessionRecord
    active: bool = False

    @property
    def session_id(self) -> str:
        return self.record.session_id

    def as_dict(self) -> dict[str, Any]:
        return session_record_dict(self.record)


@dataclass(frozen=True, slots=True)
class SessionListView:
    choices: tuple[SessionChoice, ...]
    active_session_id: str | None = None

    @property
    def records(self) -> list[SessionRecord]:
        return [choice.record for choice in self.choices]

    @property
    def session_ids(self) -> list[str]:
        return [choice.session_id for choice in self.choices]

    def text(self, *, table: bool = False) -> str:
        if table:
            return format_sessions_table(self)
        return format_sessions_markdown(self)


@dataclass(frozen=True, slots=True)
class SessionSwitchResult:
    session_id: str | None = None
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.session_id is not None and not self.message


SessionChoiceResolutionKind = Literal["session_id", "out_of_range", "empty"]


@dataclass(frozen=True, slots=True)
class SessionChoiceResolution:
    kind: SessionChoiceResolutionKind
    raw: str
    session_id: str | None = None
    message: str | None = None

    @property
    def ok(self) -> bool:
        return self.kind == "session_id" and self.session_id is not None


def build_session_list_view(
    session_runtime: SessionListRuntime,
    *,
    core_id: str | None,
    active_session_id: str | None,
    limit: int,
) -> SessionListView:
    records = session_runtime.list_sessions(core_id=core_id, limit=limit)
    return session_list_view(records, active_session_id=active_session_id)


async def start_bound_session(
    runner: Any,
    route_binding: SessionRouteBindingRuntime,
    *,
    channel: str,
    conversation_key: str | None = None,
    principal_key: str | None = None,
    source: str | None = None,
    reply_to: str | None = None,
    replace_conversation_binding: bool = False,
) -> SessionSwitchResult:
    if not hasattr(runner, "start_new_session"):
        return SessionSwitchResult(message="Session reset is not available.")
    await runner.prepare_live_core()
    kwargs = {
        "channel": channel,
        "conversation_key": conversation_key,
        "source": source,
        "reply_to": reply_to,
        "replace_conversation_binding": replace_conversation_binding,
    }
    if principal_key is not None:
        kwargs["principal_key"] = principal_key
    session_id = runner.start_new_session(
        **kwargs,
    )
    route_binding.bind(runner.interaction_router, session_id)
    return SessionSwitchResult(session_id=session_id)


def resume_bound_session(
    runner: Any,
    route_binding: SessionRouteBindingRuntime,
    session_id: str,
    *,
    channel: str | None = None,
    conversation_key: str | None = None,
    principal_key: str | None = None,
    source: str | None = None,
    reply_to: str | None = None,
    replace_conversation_binding: bool = False,
) -> SessionSwitchResult:
    try:
        kwargs = {
            "channel": channel,
            "conversation_key": conversation_key,
            "source": source,
            "reply_to": reply_to,
            "replace_conversation_binding": replace_conversation_binding,
        }
        if principal_key is not None:
            kwargs["principal_key"] = principal_key
        runner.resume_session(
            session_id,
            **kwargs,
        )
    except FileNotFoundError as exc:
        return SessionSwitchResult(message=str(exc))
    route_binding.bind(runner.interaction_router, session_id)
    return SessionSwitchResult(session_id=session_id)


def session_list_view(records: list[SessionRecord], *, active_session_id: str | None = None) -> SessionListView:
    choices = tuple(
        SessionChoice(index=index, record=record, active=record.session_id == active_session_id)
        for index, record in enumerate(records, start=1)
    )
    return SessionListView(choices=choices, active_session_id=active_session_id)


def resolve_session_choice(raw: str, view: SessionListView) -> SessionChoiceResolution:
    normalized = strip_outer_wrappers(raw.strip())
    if not normalized:
        return SessionChoiceResolution(kind="empty", raw=raw, message="session id is required")
    if normalized.isdigit():
        index = int(normalized) - 1
        if index < 0 or index >= len(view.choices):
            return SessionChoiceResolution(
                kind="out_of_range",
                raw=raw,
                message=f"Session number out of range: {normalized}",
            )
        return SessionChoiceResolution(
            kind="session_id",
            raw=raw,
            session_id=view.choices[index].session_id,
        )
    return SessionChoiceResolution(kind="session_id", raw=raw, session_id=normalized)


def session_record_dict(record: SessionRecord) -> dict[str, Any]:
    return {
        "session_id": record.session_id,
        "title": record.title,
        "updated_at": record.updated_at,
        "channel": record.channel,
        "message_count": record.message_count,
        "preview": record.preview,
    }


def format_sessions_markdown(view: SessionListView) -> str:
    if not view.choices:
        return "No sessions found."
    lines = ["# Sessions"]
    for choice in view.choices:
        record = choice.record
        marker = "*" if choice.active else " "
        preview = f" - {record.preview}" if record.preview else ""
        lines.append(
            f"{choice.index}. {marker} `{record.session_id}` - {record.updated_at} - {record.message_count} msg{preview}"
        )
    return "\n".join(lines)


def format_sessions_table(view: SessionListView) -> str:
    rows = [
        (
            str(choice.index),
            "*" if choice.active else "",
            choice.record.session_id,
            choice.record.updated_at,
            choice.record.channel or "",
            str(choice.record.message_count),
            choice.record.preview or "",
        )
        for choice in view.choices
    ]
    return format_table(["#", "", "session_id", "updated", "channel", "messages", "preview"], rows, title="Sessions")


def strip_outer_wrappers(value: str) -> str:
    current = value.strip()
    while len(current) >= 2:
        first, last = current[0], current[-1]
        if (first, last) in {("`", "`"), ("'", "'"), ('"', '"'), ("<", ">"), ("[", "]")}:
            current = current[1:-1].strip()
            continue
        break
    return current
