from __future__ import annotations

import re
from typing import Any, Callable

_MDV2_ESCAPE_RE = re.compile(r"([_*\[\]()~`>#\+\-=|{}.!\\])")
_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*){1,}\|?\s*$")
RICH_MESSAGE_MAX_CHARS = 32768

def _telegram_message_id(response: dict[str, Any] | None) -> int | None:
    if not isinstance(response, dict):
        return None
    result = response.get("result")
    if isinstance(result, dict):
        message_id = result.get("message_id")
        return message_id if isinstance(message_id, int) else None
    message_id = response.get("message_id")
    return message_id if isinstance(message_id, int) else None

def utf16_len(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2

def _escape_mdv2(value: str) -> str:
    return _MDV2_ESCAPE_RE.sub(r"\\\1", value)

def _strip_mdv2(value: str) -> str:
    cleaned = re.sub(r"\\([_*\[\]()~`>#\+\-=|{}.!\\])", r"\1", value)
    cleaned = re.sub(r"\*\*([^*]+)\*\*", r"\1", cleaned)
    cleaned = re.sub(r"\*([^*]+)\*", r"\1", cleaned)
    cleaned = re.sub(r"(?<!\w)_([^_]+)_(?!\w)", r"\1", cleaned)
    cleaned = re.sub(r"~([^~]+)~", r"\1", cleaned)
    cleaned = re.sub(r"\|\|([^|]+)\|\|", r"\1", cleaned)
    return cleaned

def _split_table_row(line: str) -> list[str]:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split("|")]

def _render_table_block_for_telegram(block: list[str]) -> str:
    if len(block) < 3:
        return "\n".join(block)
    headers = _split_table_row(block[0])
    if len(headers) < 2:
        return "\n".join(block)
    rendered: list[str] = []
    for index, row in enumerate(block[2:], start=1):
        cells = _split_table_row(row)
        heading = next((cell for cell in cells if cell), f"Row {index}")
        bullets = []
        for header, value in zip(headers, cells):
            if value == heading:
                continue
            bullets.append(f"- {header}: {value}")
        rendered.append("\n".join([f"**{heading}**", *bullets]))
    return "\n\n".join(rendered)

def _wrap_markdown_tables(text: str) -> str:
    if "|" not in text or "-" not in text:
        return text
    lines = text.split("\n")
    output: list[str] = []
    in_fence = False
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            output.append(line)
            index += 1
            continue
        if not in_fence and "|" in line and index + 1 < len(lines) and _TABLE_SEPARATOR_RE.match(lines[index + 1]):
            block = [line, lines[index + 1]]
            cursor = index + 2
            while cursor < len(lines) and "|" in lines[cursor].strip():
                block.append(lines[cursor])
                cursor += 1
            output.append(_render_table_block_for_telegram(block))
            index = cursor
            continue
        output.append(line)
        index += 1
    return "\n".join(output)

def _needs_rich_telegram_rendering(content: str) -> bool:
    if not content:
        return False
    if len(content) > RICH_MESSAGE_MAX_CHARS:
        return False
    if any(_TABLE_SEPARATOR_RE.match(line) for line in content.splitlines()):
        return True
    if re.search(r"(?m)^\s*[-*]\s+\[[ xX]\]\s+", content):
        return True
    if re.search(r"(?m)^\s*</?(?:details|summary)\b", content):
        return True
    return "$$" in content

def format_telegram_markdown_v2(content: str) -> str:
    if not content:
        return content
    placeholders: dict[str, str] = {}
    counter = 0

    def placeholder(value: str) -> str:
        nonlocal counter
        key = f"\x00TG{counter}\x00"
        counter += 1
        placeholders[key] = value
        return key

    text = _wrap_markdown_tables(content)

    def protect_fenced(match: re.Match[str]) -> str:
        raw = match.group(0)
        if "\n" in raw[3:]:
            open_end = raw.index("\n") + 1
        else:
            open_end = 3
        opening = raw[:open_end]
        body = raw[open_end:-3].replace("\\", "\\\\").replace("`", "\\`")
        return placeholder(f"{opening}{body}```")

    text = re.sub(r"(```(?:[^\n]*\n)?[\s\S]*?```)", protect_fenced, text)
    text = re.sub(r"(`[^`\n]+`)", lambda m: placeholder(m.group(0).replace("\\", "\\\\")), text)

    def convert_link(match: re.Match[str]) -> str:
        label = _escape_mdv2(match.group(1))
        url = match.group(2).replace("\\", "\\\\").replace(")", "\\)")
        return placeholder(f"[{label}]({url})")

    text = re.sub(r"\[([^\]]+)\]\(([^()]*(?:\([^()]*\)[^()]*)*)\)", convert_link, text)

    def convert_header(match: re.Match[str]) -> str:
        inner = match.group(1).strip()
        inner = re.sub(r"^\*\*(.+)\*\*$", r"\1", inner)
        return placeholder(f"*{_escape_mdv2(inner)}*")

    text = re.sub(r"^#{1,6}\s+(.+)$", convert_header, text, flags=re.MULTILINE)
    text = re.sub(r"\*\*(.+?)\*\*", lambda m: placeholder(f"*{_escape_mdv2(m.group(1))}*"), text)
    text = re.sub(r"\*([^*\n]+)\*", lambda m: placeholder(f"_{_escape_mdv2(m.group(1))}_"), text)
    text = re.sub(r"~~(.+?)~~", lambda m: placeholder(f"~{_escape_mdv2(m.group(1))}~"), text)
    text = _escape_mdv2(text)
    for key in reversed(list(placeholders.keys())):
        text = text.replace(key, placeholders[key])
    return text

def _custom_unit_to_cp(value: str, budget: int, length_fn: Callable[[str], int]) -> int:
    if length_fn(value) <= budget:
        return len(value)
    low, high = 0, len(value)
    while low < high:
        mid = (low + high + 1) // 2
        if length_fn(value[:mid]) <= budget:
            low = mid
        else:
            high = mid - 1
    return low

def _inside_fenced_code(value: str) -> bool:
    return value.count("```") % 2 == 1

def split_telegram_message(content: str, *, limit: int = 4096, markdown_v2: bool = False) -> list[str]:
    if utf16_len(content) <= limit:
        return [content]
    reserve = 16
    chunks: list[str] = []
    remaining = content
    carry_fence = False
    while remaining:
        prefix = "```\n" if carry_fence else ""
        suffix_budget = utf16_len("\n```") if carry_fence else 0
        budget = max(1, limit - reserve - utf16_len(prefix) - suffix_budget)
        slice_at = _custom_unit_to_cp(remaining, budget, utf16_len)
        region = remaining[:slice_at]
        split_at = region.rfind("\n")
        if split_at < max(1, slice_at // 2):
            split_at = region.rfind(" ")
        if split_at < 1:
            split_at = slice_at
        body = remaining[:split_at].rstrip()
        remaining = remaining[split_at:].lstrip()
        chunk = prefix + body
        if _inside_fenced_code(chunk):
            chunk += "\n```"
            carry_fence = True
        else:
            carry_fence = False
        chunks.append(chunk)
    if len(chunks) <= 1:
        return chunks
    result: list[str] = []
    for index, chunk in enumerate(chunks, start=1):
        suffix = f" ({index}/{len(chunks)})"
        if markdown_v2:
            suffix = f" \\({index}/{len(chunks)}\\)"
        result.append(f"{chunk}{suffix}")
    return result
