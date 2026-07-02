from __future__ import annotations

from typing import Any

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
    return _format_table(["task_id", "status", "core", "session", "summary"], rows, title="Subagents")


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


def _format_table(headers: list[str], rows: list[tuple[Any, ...]], *, title: str) -> str:
    widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = min(36, max(widths[index], len(str(cell))))
    lines = [f"# {title}", "", " | ".join(header.ljust(widths[index]) for index, header in enumerate(headers))]
    lines.append(" | ".join("-" * width for width in widths))
    for row in rows:
        lines.append(" | ".join(_shorten(str(cell), widths[index]).ljust(widths[index]) for index, cell in enumerate(row)))
    return "\n".join(lines)


def _shorten(text: str, limit: int = 36) -> str:
    value = str(text)
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 3)] + "..."
