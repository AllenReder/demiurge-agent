from demiurge.runtime.approvals import (
    approval_button_rows,
    approval_callback_answer,
    approval_callback_data,
    approval_decision_for_action,
    approval_resolution,
    format_approval_request_text,
    format_resolved_approval_text,
    parse_approval_callback_data,
    parse_approval_response,
)
from demiurge.security.approval import ApprovalRequest


def _request(**overrides):
    data = {
        "tool_name": "terminal",
        "tool_call_id": "call_1",
        "turn_id": "turn_1",
        "capability": "terminal.exec",
        "action": "execute",
        "risk": "high",
        "summary": "Run command",
        "command": "whoami",
        "arguments_preview": {"cmd": "whoami"},
    }
    data.update(overrides)
    return ApprovalRequest(**data)


def test_parse_approval_response_maps_text_aliases_to_decisions():
    assert parse_approval_response("1", actor="TUI user").value == "allow"
    assert parse_approval_response("always", actor="TUI user").value == "always_allow_for_session"
    deny = parse_approval_response("", actor="TUI user")

    assert deny.value == "deny"
    assert deny.reason == "denied by TUI user"


def test_parse_approval_response_denies_invalid_input():
    decision = parse_approval_response("maybe")

    assert decision.value == "deny"
    assert decision.reason == "invalid approval input: maybe"


def test_approval_decision_for_action_sets_actor_reason():
    decision = approval_decision_for_action("session", actor="Telegram user")

    assert decision is not None
    assert decision.value == "always_allow_for_session"
    assert decision.reason == "approved by Telegram user for this session"
    assert approval_decision_for_action("unknown") is None


def test_approval_callback_helpers_round_trip_action():
    assert approval_callback_data("42", "deny") == "approval:42:deny"

    parsed = parse_approval_callback_data("approval:42:deny")

    assert parsed is not None
    assert parsed.approval_id == "42"
    assert parsed.action == "deny"
    assert parse_approval_callback_data("choice:42:deny") is None
    assert parse_approval_callback_data("approval:42:unknown") is None
    assert parse_approval_callback_data("approval:42") is None


def test_approval_button_rows_use_canonical_order_and_callback_data():
    assert approval_button_rows("7") == [
        [{"text": "Allow once", "callback_data": "approval:7:allow"}],
        [{"text": "Allow for session", "callback_data": "approval:7:session"}],
        [{"text": "Deny", "callback_data": "approval:7:deny"}],
    ]


def test_approval_callback_answer_uses_decision_allowed_state():
    assert approval_callback_answer(parse_approval_response("allow")) == "Approved."
    assert approval_callback_answer(parse_approval_response("deny")) == "Denied."


def test_approval_resolution_returns_resolved_title_and_detail():
    resolution = approval_resolution("session")

    assert resolution is not None
    assert resolution.title == "Approved for session"
    assert resolution.detail == "Matching requests are allowed for this session."
    assert approval_resolution("unknown") is None


def test_format_approval_request_text_includes_request_fields_and_truncates_command():
    request = _request(command="x" * 1200, target="/tmp/out.txt")

    text = format_approval_request_text(request)

    assert "## Approval required" in text
    assert "**Summary:** Run command" in text
    assert "**Tool:** `terminal`" in text
    assert "**Target:** `/tmp/out.txt`" in text
    assert "..." in text
    assert "Choose **Allow once**, **Allow for session**, or **Deny**." in text


def test_format_resolved_approval_text_includes_summary_tool_and_command():
    request = _request()

    text = format_resolved_approval_text(request, title="Denied", detail="The command was not executed.")

    assert text.startswith("## Denied")
    assert "The command was not executed." in text
    assert "**Summary:** Run command" in text
    assert "**Tool:** `terminal`" in text
    assert "whoami" in text
