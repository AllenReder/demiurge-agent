from __future__ import annotations

from typing import Any, Iterable

from demiurge.runtime.text_format import json_safe
from demiurge.runtime.tool_display import historical_tool_item, normalize_tool_display


def build_history_items(
    messages: Iterable[Any],
    events: Iterable[dict[str, Any]],
    *,
    tool_display: str = "summary",
    limit: int = 500,
) -> list[dict[str, Any]]:
    display_mode = normalize_tool_display(tool_display)
    tool_events = tool_history_events(events)
    items: list[dict[str, Any]] = []
    for message in messages:
        if _is_visible_text_message(message):
            item = _message_item(message)
            if item is not None:
                items.append(item)
            continue
        if getattr(message, "role", "") == "tool" and display_mode != "quiet":
            tool = historical_tool_item(message, tool_events, full=display_mode == "full")
            if tool is not None:
                items.append(tool)
    return items[-limit:]


def tool_history_events(events: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for event in events:
        if event.get("type") == "actions.requested":
            for action in event.get("actions") or []:
                if not isinstance(action, dict):
                    continue
                call_id = str(action.get("id") or "")
                if not call_id:
                    continue
                by_id.setdefault(call_id, {}).update(
                    {
                        "id": call_id,
                        "name": str(action.get("name") or ""),
                        "arguments": action.get("arguments") if isinstance(action.get("arguments"), dict) else {},
                    }
                )
            continue
        if event.get("type") != "action.result":
            continue
        call_id = str(event.get("tool_call_id") or "")
        if not call_id:
            continue
        by_id.setdefault(call_id, {}).update(
            {
                "id": call_id,
                "name": str(event.get("tool_name") or ""),
                "content": str(event.get("content") or ""),
                "display_output": str(event.get("display_output") or ""),
                "model_output": event.get("model_output"),
                "is_error": bool(event.get("is_error")),
                "data": event.get("data"),
            }
        )
    return by_id


def _is_visible_text_message(message: Any) -> bool:
    return bool(getattr(message, "visible", False)) and getattr(message, "role", "") in {"user", "assistant", "system"}


def _message_item(message: Any) -> dict[str, Any] | None:
    content = str(getattr(message, "content", "") or "")
    if not content:
        return None
    metadata = {
        **(getattr(message, "metadata", None) or {}),
        "message_id": message.id,
        "turn_id": getattr(message, "turn_id", None),
        "historical": True,
    }
    return {
        "id": f"history_message_{message.id}",
        "type": "message",
        "role": message.role,
        "text": content,
        "metadata": json_safe(metadata),
    }
