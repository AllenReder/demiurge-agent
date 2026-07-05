from __future__ import annotations

import json
from typing import Any, Literal

from demiurge.providers import ToolCall
from demiurge.runtime.interactions import ToolInteractionRecord
from demiurge.runtime.text_format import json_safe, shorten_text
from demiurge.sdk import ToolResult
from demiurge.tools.records import ToolExecutionRecord


ToolDisplayMode = Literal["quiet", "summary", "full"]


def normalize_tool_display(value: str | None) -> ToolDisplayMode:
    normalized = (value or "summary").strip().lower()
    if normalized in {"quiet", "summary", "full"}:
        return normalized
    return "summary"


def tool_call_start_summary(call: ToolCall) -> str:
    if call.name == "terminal":
        command = str(call.arguments.get("command") or "").strip()
        return f"$ {command}" if command else "running terminal"
    if call.name in {"read_file", "write_file", "patch"}:
        path = call.arguments.get("path") or call.arguments.get("file_path")
        return f"{call.name}: {path}" if path else call.name
    if call.arguments:
        return json.dumps(call.arguments, ensure_ascii=False, sort_keys=True)
    return "running"


def tool_result_text(result: ToolResult | None) -> str:
    if result is None:
        return ""
    return result.display_output or result.content or ""


def tool_call_item(index: int, record: ToolInteractionRecord, *, full: bool = False) -> dict[str, Any]:
    result_text = tool_result_text(record.result)
    item: dict[str, Any] = {
        "index": index,
        "name": record.call.name,
        "id": record.call.id,
        "phase": record.phase,
        "status": record.status,
        "summary": shorten_text(result_text) if result_text else shorten_text(tool_call_start_summary(record.call)),
    }
    if full:
        item.update(
            {
                "arguments": json_safe(record.call.arguments),
                "result": result_text,
                "model_output": record.result.model_output if record.result is not None else None,
            }
        )
    return item


def historical_tool_item(message: Any, events: dict[str, dict[str, Any]], *, full: bool = False) -> dict[str, Any] | None:
    metadata = message.metadata or {}
    call_id = str(metadata.get("tool_call_id") or "")
    event = events.get(call_id, {}) if call_id else {}
    name = str(event.get("name") or metadata.get("tool_name") or "")
    if not name and not getattr(message, "content", ""):
        return None
    result_text = str(event.get("display_output") or event.get("content") or getattr(message, "content", "") or "")
    tool: dict[str, Any] = {
        "index": 1,
        "name": name or "tool",
        "id": call_id or message.id,
        "status": "error" if bool(event.get("is_error") or metadata.get("is_error")) else "ok",
        "summary": shorten_text(result_text),
    }
    if full:
        tool.update(
            {
                "arguments": json_safe(event.get("arguments") if isinstance(event.get("arguments"), dict) else {}),
                "result": result_text,
                "model_output": event.get("model_output"),
            }
        )
    return {
        "id": f"history_tool_{call_id or message.id}",
        "type": "tool",
        "display": "full" if full else "summary",
        "tools": [tool],
    }


def tool_call_markdown(record: ToolInteractionRecord, *, mode: ToolDisplayMode | str = "summary") -> str:
    display_mode = normalize_tool_display(mode)
    if display_mode == "quiet":
        return ""
    if record.phase == "start" or record.result is None:
        summary = shorten_text(tool_call_start_summary(record.call), limit=220)
        return f"## Tool call\n`{record.call.name}` - `running` - {summary}"
    if display_mode == "full":
        return _finished_tool_call_markdown(record)
    result = shorten_text(tool_result_text(record.result), limit=220)
    return f"## Tool call\n`{record.call.name}` - `{record.status}` - {result}"


def tool_results_markdown(records: list[ToolExecutionRecord], *, mode: ToolDisplayMode | str = "summary") -> str:
    display_mode = normalize_tool_display(mode)
    if display_mode == "quiet" or not records:
        return ""
    if display_mode == "full":
        sections: list[str] = ["## Tool calls"]
        for index, record in enumerate(records, start=1):
            sections.extend(_tool_result_full_section(index, record))
        return "\n".join(sections)

    lines = ["## Tool calls"]
    for index, record in enumerate(records, start=1):
        status = "error" if record.result.is_error else "ok"
        result = shorten_text(tool_result_text(record.result), limit=220)
        lines.append(f"{index}. `{record.call.name}` - `{status}` - {result}")
    return "\n".join(lines)


def _finished_tool_call_markdown(record: ToolInteractionRecord) -> str:
    assert record.result is not None
    status = record.status
    sections = [
        "## Tool call",
        "",
        f"### `{record.call.name}` - `{status}`",
        "",
        "**Arguments**",
        "```json",
        shorten_text(json.dumps(record.call.arguments, ensure_ascii=False, indent=2), limit=1800),
        "```",
        "",
        "**Result**",
        "```",
        shorten_text(tool_result_text(record.result), limit=1800),
        "```",
    ]
    _append_model_output(sections, record.result)
    return "\n".join(sections)


def _tool_result_full_section(index: int, record: ToolExecutionRecord) -> list[str]:
    status = "error" if record.result.is_error else "ok"
    section = [
        "",
        f"### {index}. `{record.call.name}` - `{status}`",
        "",
        "**Arguments**",
        "```json",
        shorten_text(json.dumps(record.call.arguments, ensure_ascii=False, indent=2), limit=1800),
        "```",
        "",
        "**Result**",
        "```",
        shorten_text(tool_result_text(record.result), limit=1800),
        "```",
    ]
    _append_model_output(section, record.result)
    return section


def _append_model_output(sections: list[str], result: ToolResult) -> None:
    if result.model_output and result.model_output != result.content:
        sections.extend(
            [
                "",
                "**Model output**",
                "```",
                shorten_text(result.model_output, limit=1200),
                "```",
            ]
        )
