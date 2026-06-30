from __future__ import annotations

import os
import re
from pathlib import Path


_ENV_KEY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_ENV_LINE_RE = re.compile(r"^\s*(?:export\s+)?(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*=")


def runtime_env_path(home: Path) -> Path:
    return home / ".env"


def load_runtime_env(home: Path) -> dict[str, str]:
    path = runtime_env_path(home)
    if not path.exists():
        return {}
    values = parse_env_text(path.read_text(encoding="utf-8"))
    for key, value in values.items():
        os.environ[key] = value
    return values


def parse_env_text(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        if "=" not in line:
            raise ValueError(f"invalid .env line {line_number}: expected KEY=VALUE")
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if not _ENV_KEY_RE.fullmatch(key):
            raise ValueError(f"invalid .env line {line_number}: invalid key `{key}`")
        values[key] = _parse_env_value(raw_value.strip())
    return values


def upsert_env_value(path: Path, key: str, value: str) -> None:
    if not _ENV_KEY_RE.fullmatch(key):
        raise ValueError(f"invalid env key: {key}")
    path.parent.mkdir(parents=True, exist_ok=True)
    line = f"{key}={quote_env_value(value)}"
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    replaced = False
    next_lines: list[str] = []
    for existing in lines:
        match = _ENV_LINE_RE.match(existing)
        if match and match.group("key") == key:
            if not replaced:
                next_lines.append(line)
                replaced = True
            continue
        next_lines.append(existing)
    if not replaced:
        if next_lines and next_lines[-1].strip():
            next_lines.append("")
        next_lines.append(line)
    path.write_text("\n".join(next_lines) + "\n", encoding="utf-8")


def quote_env_value(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r")
    return f'"{escaped}"'


def _parse_env_value(raw: str) -> str:
    if not raw:
        return ""
    if raw[0] == raw[-1:] == "'":
        return raw[1:-1].replace("\\'", "'")
    if raw[0] == raw[-1:] == '"':
        return _decode_double_quoted(raw[1:-1])
    return _strip_inline_comment(raw).strip()


def _decode_double_quoted(value: str) -> str:
    result: list[str] = []
    index = 0
    while index < len(value):
        char = value[index]
        if char != "\\" or index + 1 >= len(value):
            result.append(char)
            index += 1
            continue
        escaped = value[index + 1]
        result.append({"n": "\n", "r": "\r", "t": "\t", '"': '"', "\\": "\\"}.get(escaped, escaped))
        index += 2
    return "".join(result)


def _strip_inline_comment(value: str) -> str:
    for index, char in enumerate(value):
        if char == "#" and (index == 0 or value[index - 1].isspace()):
            return value[:index]
    return value
