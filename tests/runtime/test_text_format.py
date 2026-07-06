from __future__ import annotations

from dataclasses import dataclass

from demiurge.runtime.text_format import format_key_values, format_table, json_safe, shorten_text


@dataclass
class Payload:
    name: str
    count: int


def test_shorten_text_normalizes_whitespace_and_marks_truncation():
    assert shorten_text("alpha\n beta", limit=20) == "alpha beta"
    assert shorten_text("abcdefghijklmnopqrstuvwxyz", limit=18) == "abcd...[truncated]"
    assert shorten_text("abcdefghijklmnopqrstuvwxyz", limit=3) == "abc"


def test_shorten_text_can_preserve_legacy_plain_marker():
    assert shorten_text("abcdefghijklmnopqrstuvwxyz", limit=8, marker="...", normalize_whitespace=False) == "abcde..."


def test_json_safe_coerces_dataclasses_nested_values_and_unknown_objects():
    class Unknown:
        def __str__(self) -> str:
            return "unknown"

    assert json_safe({"payload": Payload("demo", 2), "items": (Unknown(),)}) == {
        "payload": {"name": "demo", "count": 2},
        "items": ["unknown"],
    }


def test_format_table_uses_title_widths_and_truncation():
    text = format_table(
        ["name", "value"],
        [("short", "abcdefghijklmnopqrstuvwxyz")],
        title="Demo",
        max_column_width=12,
    )

    assert text.splitlines() == [
        "## Demo",
        "",
        "name  | value       ",
        "----- | ------------",
        "short | abcdefghijkl",
    ]


def test_format_table_can_preserve_delegation_heading_and_marker_style():
    text = format_table(
        ["task_id", "summary"],
        [("task_1", "abcdefghijklmnopqrstuvwxyz")],
        title="Subagents",
        title_level=1,
        max_column_width=10,
        truncation_marker="...",
        normalize_whitespace=False,
    )

    assert text.splitlines() == [
        "# Subagents",
        "",
        "task_id | summary   ",
        "------- | ----------",
        "task_1  | abcdefg...",
    ]


def test_format_key_values_json_encodes_structured_values():
    text = format_key_values("Status", {"core": "assistant", "payload": {"b": 2}, "dataclass": Payload("demo", 2)})

    assert "## Status" in text
    assert "core" in text
    assert '{"b": 2}' in text
    assert '{"name": "demo", "count": 2}' in text
