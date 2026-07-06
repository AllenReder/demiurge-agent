from __future__ import annotations

from demiurge.runtime.history_display import build_history_items, tool_history_events
from demiurge.storage import SessionMessage


def _message(
    role: str,
    content: str,
    *,
    message_id: str = "msg_1",
    turn_id: str | None = "turn_1",
    visible: bool = True,
    metadata: dict | None = None,
) -> SessionMessage:
    return SessionMessage(
        id=message_id,
        session_id="session_1",
        turn_id=turn_id,
        role=role,
        content=content,
        created_at="2026-07-06T00:00:00Z",
        visible=visible,
        metadata=metadata,
    )


def test_build_history_items_maps_visible_messages_with_historical_metadata():
    items = build_history_items(
        [
            _message("user", "hello", message_id="msg_user", metadata={"source": "local"}),
            _message("assistant", "hi", message_id="msg_assistant"),
            _message("system", "notice", message_id="msg_system", turn_id=None),
        ],
        [],
    )

    assert items == [
        {
            "id": "history_message_msg_user",
            "type": "message",
            "role": "user",
            "text": "hello",
            "metadata": {"source": "local", "message_id": "msg_user", "turn_id": "turn_1", "historical": True},
        },
        {
            "id": "history_message_msg_assistant",
            "type": "message",
            "role": "assistant",
            "text": "hi",
            "metadata": {"message_id": "msg_assistant", "turn_id": "turn_1", "historical": True},
        },
        {
            "id": "history_message_msg_system",
            "type": "message",
            "role": "system",
            "text": "notice",
            "metadata": {"message_id": "msg_system", "turn_id": None, "historical": True},
        },
    ]


def test_build_history_items_skips_invisible_empty_and_unsupported_messages():
    items = build_history_items(
        [
            _message("user", "hidden", visible=False),
            _message("assistant", ""),
            _message("debug", "debug text"),
        ],
        [],
    )

    assert items == []


def test_build_history_items_suppresses_tool_messages_in_quiet_mode():
    items = build_history_items(
        [_message("tool", "tool content", message_id="msg_tool", metadata={"tool_call_id": "call_1"})],
        [{"type": "action.result", "tool_call_id": "call_1", "tool_name": "terminal", "content": "done"}],
        tool_display="quiet",
    )

    assert items == []


def test_build_history_items_reconstructs_full_tool_cards():
    items = build_history_items(
        [_message("tool", "fallback", message_id="msg_tool", metadata={"tool_call_id": "call_1"})],
        [
            {"type": "actions.requested", "actions": [{"id": "call_1", "name": "terminal", "arguments": {"command": "whoami"}}]},
            {
                "type": "action.result",
                "tool_call_id": "call_1",
                "tool_name": "terminal",
                "content": "alice",
                "display_output": "$ whoami\nalice",
                "model_output": "model sees alice",
            },
        ],
        tool_display="full",
    )

    assert items == [
        {
            "id": "history_tool_call_1",
            "type": "tool",
            "display": "full",
            "tools": [
                {
                    "index": 1,
                    "name": "terminal",
                    "id": "call_1",
                    "status": "ok",
                    "summary": "$ whoami alice",
                    "arguments": {"command": "whoami"},
                    "result": "$ whoami\nalice",
                    "model_output": "model sees alice",
                }
            ],
        }
    ]


def test_build_history_items_limits_to_latest_items():
    items = build_history_items(
        [_message("user", str(index), message_id=f"msg_{index}") for index in range(3)],
        [],
        limit=2,
    )

    assert [item["text"] for item in items] == ["1", "2"]


def test_tool_history_events_combines_requested_and_result_events():
    events = tool_history_events(
        [
            {"type": "actions.requested", "actions": [{"id": "call_1", "name": "terminal", "arguments": {"command": "pwd"}}]},
            {"type": "action.result", "tool_call_id": "call_1", "tool_name": "terminal", "content": "ok", "is_error": True},
        ]
    )

    assert events["call_1"] == {
        "id": "call_1",
        "name": "terminal",
        "arguments": {"command": "pwd"},
        "content": "ok",
        "display_output": "",
        "model_output": None,
        "is_error": True,
        "data": None,
    }
