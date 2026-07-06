from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from typing import Any


def shorten_text(
    text: Any,
    limit: int = 160,
    *,
    marker: str = "...[truncated]",
    normalize_whitespace: bool = True,
) -> str:
    value = str(text)
    if normalize_whitespace:
        value = " ".join(value.split())
    if len(value) <= limit:
        return value
    if limit <= len(marker):
        return value[:limit]
    return f"{value[: limit - len(marker)]}{marker}"


def json_safe(value: Any) -> Any:
    if is_dataclass(value):
        return json_safe(asdict(value))
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def format_table(
    headers: list[str],
    rows: list[tuple[Any, ...]],
    *,
    title: str | None = None,
    title_level: int = 2,
    max_column_width: int = 72,
    truncation_marker: str = "...[truncated]",
    normalize_whitespace: bool = True,
) -> str:
    table_rows = [[str(cell) for cell in row] for row in rows]
    widths = [len(header) for header in headers]
    for row in table_rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], min(len(cell), max_column_width))
    lines = [f"{'#' * title_level} {title}", ""] if title else []
    lines.append(" | ".join(header.ljust(widths[index]) for index, header in enumerate(headers)))
    lines.append(" | ".join("-" * width for width in widths))
    for row in table_rows:
        lines.append(
            " | ".join(
                shorten_text(
                    cell,
                    limit=widths[index],
                    marker=truncation_marker,
                    normalize_whitespace=normalize_whitespace,
                ).ljust(widths[index])
                for index, cell in enumerate(row)
            )
        )
    return "\n".join(lines)


def format_key_values(title: str, values: dict[str, Any]) -> str:
    def rendered(value: Any) -> str:
        safe_value = json_safe(value)
        if isinstance(safe_value, (dict, list)):
            return json.dumps(safe_value, ensure_ascii=False)
        return str(safe_value)

    rows = [
        (
            str(key),
            rendered(value),
        )
        for key, value in values.items()
    ]
    return format_table(["key", "value"], rows, title=title)
