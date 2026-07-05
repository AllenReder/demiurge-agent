from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from demiurge.runtime.text_format import shorten_text
from demiurge.security.approval import ApprovalDecision, ApprovalRequest


@dataclass(frozen=True, slots=True)
class ApprovalCallback:
    approval_id: str
    action: str


@dataclass(frozen=True, slots=True)
class ApprovalResolution:
    title: str
    detail: str


_TEXT_ALIASES = {
    "1": "allow",
    "y": "allow",
    "yes": "allow",
    "allow": "allow",
    "approve": "allow",
    "once": "allow",
    "2": "session",
    "a": "session",
    "always": "session",
    "session": "session",
    "always_allow_for_session": "session",
    "3": "deny",
    "n": "deny",
    "no": "deny",
    "deny": "deny",
    "": "deny",
}

_DECISIONS = {
    "allow": ApprovalDecision("allow", "approved by user"),
    "session": ApprovalDecision("always_allow_for_session", "approved by user for this session"),
    "deny": ApprovalDecision("deny", "denied by user"),
}

_BUTTON_LABELS = {
    "allow": "Allow once",
    "session": "Allow for session",
    "deny": "Deny",
}

_RESOLUTIONS = {
    "allow": ApprovalResolution("Approved once", "The command was approved for this request."),
    "session": ApprovalResolution("Approved for session", "Matching requests are allowed for this session."),
    "deny": ApprovalResolution("Denied", "The command was not executed."),
}


def parse_approval_response(text: Any, *, actor: str = "user") -> ApprovalDecision:
    normalized = str(text or "").strip().lower()
    action = _TEXT_ALIASES.get(normalized)
    if action is None:
        return ApprovalDecision("deny", f"invalid approval input: {text}")
    return approval_decision_for_action(action, actor=actor) or ApprovalDecision("deny", f"invalid approval input: {text}")


def approval_decision_for_action(action: Any, *, actor: str = "user") -> ApprovalDecision | None:
    decision = _DECISIONS.get(str(action or "").strip().lower())
    if decision is None:
        return None
    return ApprovalDecision(decision.value, _decision_reason(decision.value, actor=actor))


def approval_callback_data(approval_id: str, action: str, *, prefix: str = "approval") -> str:
    return f"{prefix}:{approval_id}:{action}"


def parse_approval_callback_data(data: Any, *, prefix: str = "approval") -> ApprovalCallback | None:
    parts = str(data or "").split(":")
    if len(parts) != 3 or parts[0] != prefix:
        return None
    _, approval_id, action = parts
    if not approval_id or action not in _DECISIONS:
        return None
    return ApprovalCallback(approval_id=approval_id, action=action)


def approval_button_rows(approval_id: str, *, prefix: str = "approval") -> list[list[dict[str, str]]]:
    rows: list[list[dict[str, str]]] = []
    for action in ("allow", "session", "deny"):
        rows.append(
            [
                {
                    "text": _BUTTON_LABELS[action],
                    "callback_data": approval_callback_data(approval_id, action, prefix=prefix),
                }
            ]
        )
    return rows


def approval_callback_answer(decision: ApprovalDecision) -> str:
    return "Approved." if decision.allowed else "Denied."


def approval_resolution(action: Any) -> ApprovalResolution | None:
    return _RESOLUTIONS.get(str(action or "").strip().lower())


def format_approval_request_text(
    request: ApprovalRequest,
    *,
    command_limit: int = 1000,
    arguments_limit: int = 1000,
    expires_text: str = "This request expires in 10 minutes.",
) -> str:
    lines = [
        "## Approval required",
        "",
        f"**Summary:** {request.summary}",
        f"**Tool:** `{request.tool_name}`",
        f"**Risk:** `{request.risk}`",
        f"**Capability:** `{request.capability}`",
        f"**Action:** `{request.action}`",
    ]
    if request.target:
        lines.append(f"**Target:** `{request.target}`")
    if request.command:
        command = shorten_text(
            request.command,
            limit=command_limit,
            marker="...",
            normalize_whitespace=False,
        )
        lines.extend(["", "**Command**", "```", command, "```"])
    if request.arguments_preview:
        preview = json.dumps(request.arguments_preview, ensure_ascii=False, sort_keys=True, indent=2)
        arguments = shorten_text(
            preview,
            limit=arguments_limit,
            marker="...",
            normalize_whitespace=False,
        )
        lines.extend(["", "**Arguments**", "```json", arguments, "```"])
    lines.extend(["", expires_text, "Choose **Allow once**, **Allow for session**, or **Deny**."])
    return "\n".join(lines)


def format_resolved_approval_text(
    request: ApprovalRequest,
    *,
    title: str,
    detail: str,
    command_limit: int = 1000,
) -> str:
    lines = [
        f"## {title}",
        "",
        detail,
        "",
        f"**Summary:** {request.summary}",
        f"**Tool:** `{request.tool_name}`",
    ]
    if request.command:
        command = shorten_text(
            request.command,
            limit=command_limit,
            marker="...",
            normalize_whitespace=False,
        )
        lines.extend(["", "**Command**", "```", command, "```"])
    return "\n".join(lines)


def _decision_reason(value: str, *, actor: str) -> str:
    if value == "allow":
        return f"approved by {actor}"
    if value == "always_allow_for_session":
        return f"approved by {actor} for this session"
    return f"denied by {actor}"
