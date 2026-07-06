from __future__ import annotations

import json
from typing import Any, Mapping, Protocol, Sequence

from demiurge.runtime.text_format import format_table, shorten_text


class EventLogRuntime(Protocol):
    def for_turn(self, turn_id: str) -> list[dict[str, Any]]:
        ...

    def tail(self, limit: int, *, event_type: str | None = None) -> list[dict[str, Any]]:
        ...


class LatestTurnRuntime(Protocol):
    def latest_turn_id(self, session_id: str) -> str | None:
        ...


def build_trace_command_text(
    event_log: EventLogRuntime,
    session_runtime: LatestTurnRuntime,
    *,
    session_id: str,
    display_turns: Sequence[Mapping[str, Any]],
    args: str,
) -> str:
    turn_id = args.strip() or "last"
    if turn_id == "last":
        if display_turns:
            turn_id = str(display_turns[-1]["turn_id"])
        else:
            latest = session_runtime.latest_turn_id(session_id)
            if latest:
                turn_id = latest
            else:
                return "no turns yet"

    events = event_log.for_turn(turn_id)
    if not events:
        latest = session_runtime.latest_turn_id(session_id)
        if latest and latest != turn_id:
            events = event_log.for_turn(latest)
            turn_id = latest
    if not events:
        return "no turns yet"

    rows = [(str(event.get("created_at", "")), str(event.get("type", "")), event_detail(event)) for event in events]
    return format_table(["time", "type", "detail"], rows, title=f"Trace {turn_id}")


def build_events_command_text(event_log: EventLogRuntime, *, args: str) -> str:
    event_type: str | None = None
    limit = 10
    parts = args.split()
    if parts:
        if parts[0].isdigit():
            limit = int(parts[0])
        else:
            event_type = parts[0]
            if len(parts) > 1 and parts[1].isdigit():
                limit = int(parts[1])
    rows = [
        (str(event.get("created_at", "")), str(event.get("type", "")), str(event.get("turn_id", "")), event_detail(event))
        for event in event_log.tail(limit, event_type=event_type)
    ]
    return format_table(["time", "type", "turn", "detail"], rows, title="Events")


def event_detail(event: dict[str, Any]) -> str:
    event_type = event.get("type")
    if event_type == "actions.requested":
        actions = event.get("actions") or []
        return ", ".join(str(action.get("name")) for action in actions if isinstance(action, dict))
    if event_type == "action.result":
        status = "error" if event.get("is_error") else "ok"
        return f"{event.get('tool_name')} {status}: {shorten_text(str(event.get('content') or ''))}"
    if event_type and str(event_type).startswith("approval."):
        return " ".join(
            str(part)
            for part in [event.get("tool_name"), event.get("decision"), event.get("reason"), event.get("summary")]
            if part
        )
    if event_type == "message.completed":
        return shorten_text(str(event.get("content") or ""))
    return shorten_text(
        json.dumps(
            {key: value for key, value in event.items() if key not in {"id", "created_at", "type", "session_id"}},
            ensure_ascii=False,
        )
    )
