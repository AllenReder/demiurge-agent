from __future__ import annotations

from typing import Any

from demiurge.runtime.event_commands import build_events_command_text, build_trace_command_text, event_detail


class FakeEventLog:
    def __init__(self, *, by_turn: dict[str, list[dict[str, Any]]] | None = None, events: list[dict[str, Any]] | None = None):
        self.by_turn = by_turn or {}
        self.events = events or []
        self.tail_calls: list[dict[str, Any]] = []

    def for_turn(self, turn_id: str) -> list[dict[str, Any]]:
        return list(self.by_turn.get(turn_id, []))

    def tail(self, limit: int, *, event_type: str | None = None) -> list[dict[str, Any]]:
        self.tail_calls.append({"limit": limit, "event_type": event_type})
        values = [event for event in self.events if event_type is None or event.get("type") == event_type]
        return values[-limit:]


class FakeSessionRuntime:
    def __init__(self, latest: str | None):
        self.latest = latest
        self.calls: list[str] = []

    def latest_turn_id(self, session_id: str) -> str | None:
        self.calls.append(session_id)
        return self.latest


def test_trace_last_uses_display_turn_metadata_before_persisted_latest():
    text = build_trace_command_text(
        FakeEventLog(
            by_turn={
                "turn_display": [
                    {"created_at": "2026-07-06T01:00:00Z", "type": "message.completed", "content": "done"}
                ]
            }
        ),
        FakeSessionRuntime("turn_latest"),
        session_id="session_1",
        display_turns=[{"turn_id": "turn_display"}],
        args="last",
    )

    assert "## Trace turn_display" in text
    assert "message.completed" in text
    assert "done" in text


def test_trace_falls_back_from_missing_requested_turn_to_latest_turn():
    text = build_trace_command_text(
        FakeEventLog(
            by_turn={
                "turn_latest": [
                    {
                        "created_at": "2026-07-06T01:00:00Z",
                        "type": "action.result",
                        "tool_name": "terminal",
                        "content": "ok",
                    }
                ]
            }
        ),
        FakeSessionRuntime("turn_latest"),
        session_id="session_1",
        display_turns=[],
        args="turn_missing",
    )

    assert "## Trace turn_latest" in text
    assert "terminal ok: ok" in text


def test_trace_without_any_turns_reports_no_turns_yet():
    text = build_trace_command_text(
        FakeEventLog(),
        FakeSessionRuntime(None),
        session_id="session_1",
        display_turns=[],
        args="last",
    )

    assert text == "no turns yet"


def test_events_command_parses_type_and_limit():
    event_log = FakeEventLog(
        events=[
            {"created_at": "1", "type": "message.completed", "turn_id": "turn_1", "content": "first"},
            {"created_at": "2", "type": "action.result", "turn_id": "turn_2", "tool_name": "terminal", "content": "second"},
            {"created_at": "3", "type": "action.result", "turn_id": "turn_3", "tool_name": "patch", "content": "third"},
        ]
    )

    text = build_events_command_text(event_log, args="action.result 1")

    assert event_log.tail_calls == [{"limit": 1, "event_type": "action.result"}]
    assert "## Events" in text
    assert "turn_3" in text
    assert "turn_2" not in text
    assert "patch ok: third" in text


def test_events_command_parses_limit_only():
    event_log = FakeEventLog(
        events=[
            {"created_at": "1", "type": "message.completed", "turn_id": "turn_1", "content": "first"},
            {"created_at": "2", "type": "message.completed", "turn_id": "turn_2", "content": "second"},
        ]
    )

    build_events_command_text(event_log, args="1")

    assert event_log.tail_calls == [{"limit": 1, "event_type": None}]


def test_event_detail_summarizes_known_and_fallback_events():
    assert event_detail({"type": "actions.requested", "actions": [{"name": "terminal"}, {"name": "patch"}]}) == "terminal, patch"
    assert event_detail({"type": "action.result", "tool_name": "terminal", "content": "done", "is_error": True}) == "terminal error: done"
    assert event_detail(
        {
            "type": "approval.resolved",
            "tool_name": "terminal",
            "decision": "deny",
            "reason": "policy",
            "summary": "rm",
        }
    ) == "terminal deny policy rm"
    assert event_detail({"type": "message.completed", "content": "assistant text"}) == "assistant text"
    assert event_detail({"type": "custom", "id": "1", "created_at": "now", "session_id": "s", "value": {"x": 1}}) == '{"value": {"x": 1}}'
