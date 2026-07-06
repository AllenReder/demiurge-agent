from __future__ import annotations

from typing import Any

from demiurge.runtime.text_format import format_table, shorten_text
from demiurge.runtime.tasks import RuntimeTaskWorker


async def subagents_command_text(task_worker: RuntimeTaskWorker, *, session_id: str, args: str) -> str:
    parts = args.split()
    if parts and parts[0] == "cancel":
        if len(parts) != 2:
            return "Usage: /subagents cancel <task_id>"
        try:
            record = await task_worker.cancel(parts[1])
        except KeyError:
            return f"Subagent task not found: {parts[1]}"
        return _format_task(record.to_payload(include_log=True, log=task_worker.log(record.task_id)), title="Cancelled Subagent")
    if parts:
        task_id = parts[0]
        try:
            record = task_worker.get(task_id)
        except KeyError:
            return f"Subagent task not found: {task_id}"
        if record.kind != "agent.spawn":
            return f"Task is not a subagent: {task_id}"
        return _format_task(record.to_payload(include_log=True, log=task_worker.log(record.task_id)), title="Subagent")
    records = task_worker.list_tasks(owner_session_id=session_id, kind="agent.spawn")
    if not records:
        return "No subagents for this session."
    rows = [
        (
            record.task_id,
            record.status,
            str(record.metadata.get("child_core_id") or ""),
            str(record.metadata.get("child_session_id") or ""),
            _shorten(record.summary or ""),
        )
        for record in records
    ]
    return format_table(
        ["task_id", "status", "core", "session", "summary"],
        rows,
        title="Subagents",
        title_level=1,
        max_column_width=36,
        truncation_marker="...",
        normalize_whitespace=False,
    )


def _format_task(payload: dict[str, Any], *, title: str) -> str:
    lines = [f"# {title}", ""]
    for key in ("task_id", "kind", "status", "owner_session_id", "owner_turn_id", "result_ref", "summary"):
        value = payload.get(key)
        if value not in (None, ""):
            lines.append(f"- {key}: `{value}`")
    metadata = payload.get("metadata")
    if isinstance(metadata, dict) and metadata:
        lines.extend(["", "## Metadata"])
        for key in sorted(metadata):
            lines.append(f"- {key}: `{metadata[key]}`")
    log = payload.get("log")
    if isinstance(log, list) and log:
        lines.extend(["", "## Log", "```text"])
        lines.extend(str(line) for line in log[-40:])
        lines.append("```")
    return "\n".join(lines)


def _shorten(text: str, limit: int = 36) -> str:
    return shorten_text(text, limit=limit, marker="...", normalize_whitespace=False)
