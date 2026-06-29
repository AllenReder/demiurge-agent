from __future__ import annotations

import json
import os
import re
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Mapping

import yaml

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None

try:
    import msvcrt
except ImportError:  # pragma: no cover - Unix fallback
    msvcrt = None


ENTRY_DELIMITER = "\n§\n"
SNAPSHOT_FILENAME = "memory_basic_snapshot.json"
DEFAULT_CONFIG: dict[str, Any] = {
    "storage": {
        "relative_to": "core_root",
        "path": "memory",
    },
    "snapshot": {
        "mode": "session",
    },
    "limits": {
        "memory_chars": 2200,
        "user_chars": 1375,
    },
}
TARGET_FILENAMES = {
    "memory": "MEMORY.md",
    "user": "USER.md",
}
TARGET_LABELS = {
    "memory": "MEMORY (your personal notes)",
    "user": "USER PROFILE (who the user is)",
}

_THREAT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"ignore\s+(?:\w+\s+){0,8}(previous|all|above|prior)\s+(?:\w+\s+){0,8}instructions", re.I),
        "prompt_injection",
    ),
    (
        re.compile(r"disregard\s+(?:\w+\s+){0,8}(your|all|any)\s+(?:\w+\s+){0,8}(instructions|rules|guidelines)", re.I),
        "disregard_rules",
    ),
    (re.compile(r"system\s+prompt\s+override", re.I), "system_prompt_override"),
    (re.compile(r"<!--[^>]*(?:ignore|override|system|secret|hidden)[^>]*-->", re.I), "html_comment_injection"),
    (re.compile(r"\b(?:curl|wget)\b[^\n]*(?:\.env|token|secret|credentials|api[_-]?key)", re.I), "exfiltration"),
    (re.compile(r"\bcat\s+[^\n]*(?:\.env|credentials|\.netrc|\.pgpass|\.npmrc|\.pypirc)", re.I), "read_secrets"),
    (re.compile(r"(?:api[_-]?key|token|secret|password)\s*[=:]\s*[\"'][A-Za-z0-9+/=_-]{20,}", re.I), "hardcoded_secret"),
)
_BIDI_CONTROLS = {"\u202a", "\u202b", "\u202c", "\u202d", "\u202e", "\u2066", "\u2067", "\u2068", "\u2069"}


def resolve_core_root(slot_file: str | Path) -> Path:
    path = Path(slot_file).resolve()
    start = path if path.is_dir() else path.parent
    for candidate in [start, *start.parents]:
        if (candidate / "agent.yaml").exists() and (candidate / "agent").is_dir():
            return candidate
    raise ValueError(f"could not resolve core root from slot path: {slot_file}")


def load_memory_config(slot_file: str | Path) -> dict[str, Any]:
    core_root = resolve_core_root(slot_file)
    config = _deep_merge(DEFAULT_CONFIG, _load_yaml_mapping(core_root / "agent" / "lib" / "memory_basic" / "config.yaml"))
    config = _deep_merge(config, _load_yaml_mapping(Path(slot_file).with_name("config.yaml")))
    config["core_root"] = str(core_root)
    return config


def load_or_create_session_snapshot(slot_file: str | Path, session_root: str | Path) -> dict[str, Any]:
    config = load_memory_config(slot_file)
    snapshot_path = Path(session_root) / SNAPSHOT_FILENAME
    loaded = _load_json_mapping(snapshot_path)
    if loaded:
        return loaded
    store = MemoryStore.from_config(config)
    snapshot = store.snapshot()
    _write_json_atomic(snapshot_path, snapshot)
    return snapshot


def snapshot_blocks(snapshot: Mapping[str, Any]) -> dict[str, str]:
    blocks = snapshot.get("blocks")
    if not isinstance(blocks, Mapping):
        return {}
    return {str(key): str(value) for key, value in blocks.items() if value}


class MemoryStore:
    def __init__(self, *, core_root: Path, storage_dir: Path, memory_char_limit: int, user_char_limit: int) -> None:
        self.core_root = core_root
        self.storage_dir = storage_dir
        self.memory_char_limit = memory_char_limit
        self.user_char_limit = user_char_limit

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> "MemoryStore":
        core_root = Path(str(config.get("core_root") or "")).resolve()
        if not core_root:
            raise ValueError("memory config is missing core_root")
        limits = _mapping(config.get("limits"))
        return cls(
            core_root=core_root,
            storage_dir=_resolve_storage_dir(core_root, _mapping(config.get("storage"))),
            memory_char_limit=_positive_int(limits.get("memory_chars"), 2200),
            user_char_limit=_positive_int(limits.get("user_chars"), 1375),
        )

    def snapshot(self) -> dict[str, Any]:
        blocks: dict[str, str] = {}
        for target in ("memory", "user"):
            entries = self.read_entries(target, create=True)
            sanitized = self._sanitize_entries_for_snapshot(entries, target)
            block = self.render_block(target, sanitized)
            if block:
                blocks[target] = block
        return {
            "schema_version": 1,
            "storage_dir": str(self.storage_dir),
            "limits": {
                "memory_chars": self.memory_char_limit,
                "user_chars": self.user_char_limit,
            },
            "blocks": blocks,
        }

    def apply_tool_args(self, args: Mapping[str, Any]) -> dict[str, Any]:
        target = str(args.get("target") or "memory").strip()
        action = str(args.get("action") or "").strip()
        if action == "list":
            return self.list_entries(target)
        if target not in TARGET_FILENAMES:
            return {
                "success": False,
                "error": f"Invalid target '{target}'. Use 'memory' or 'user'; target 'all' is only valid with action=list.",
            }
        operations = args.get("operations")
        if operations is not None:
            if not isinstance(operations, list):
                return {"success": False, "error": "operations must be a list of objects."}
            return self.apply_batch(target, operations)
        content = str(args.get("content") or "")
        old_text = str(args.get("old_text") or "")
        if action == "add":
            return self.add(target, content)
        if action == "replace":
            return self.replace(target, old_text, content)
        if action == "remove":
            return self.remove(target, old_text)
        return {"success": False, "error": "Unknown action. Use add, replace, remove, list, or operations."}

    def list_entries(self, target: str) -> dict[str, Any]:
        if target == "all":
            targets = ("memory", "user")
        elif target in TARGET_FILENAMES:
            targets = (target,)
        else:
            return {"success": False, "error": f"Invalid target '{target}'. Use 'memory', 'user', or 'all'."}

        stores: dict[str, dict[str, Any]] = {}
        for current_target in targets:
            entries = self.read_entries(current_target, create=True)
            current = self._char_count(entries)
            limit = self._char_limit(current_target)
            pct = min(100, int((current / limit) * 100)) if limit > 0 else 0
            stores[current_target] = {
                "entries": entries,
                "entry_count": len(entries),
                "char_count": current,
                "char_limit": limit,
                "usage": f"{pct}% - {current:,}/{limit:,} chars",
            }

        memory_count = stores.get("memory", {}).get("entry_count", 0)
        user_count = stores.get("user", {}).get("entry_count", 0)
        if target == "all":
            message = f"Listed {_entry_count_label(memory_count, 'memory')} and {_entry_count_label(user_count, 'user')}."
        else:
            count = stores[target]["entry_count"]
            label = "memory" if target == "memory" else "user"
            message = f"Listed {_entry_count_label(count, label)}."
        return {
            "success": True,
            "done": True,
            "action": "list",
            "target": target,
            "message": message,
            "stores": stores,
            "usage": {key: value["usage"] for key, value in stores.items()},
            "entry_count": sum(value["entry_count"] for value in stores.values()),
        }

    def add(self, target: str, content: str) -> dict[str, Any]:
        content = content.strip()
        if not content:
            return {"success": False, "error": "content is required for add."}
        threat = first_threat_id(content)
        if threat:
            return _threat_error(threat)
        path = self.path_for(target)
        with _file_lock(path):
            entries = self.read_entries(target, create=True)
            if content in entries:
                return self._success_response(target, entries, "Entry already exists; no duplicate added.")
            next_entries = [*entries, content]
            over = self._over_limit(target, next_entries)
            if over is not None:
                return self._overflow_error(target, entries, len(content), over)
            self._write_entries(path, next_entries)
            return self._success_response(target, next_entries, "Entry added.")

    def replace(self, target: str, old_text: str, content: str) -> dict[str, Any]:
        old_text = old_text.strip()
        content = content.strip()
        if not old_text:
            return self._missing_old_text_error(target, "replace")
        if not content:
            return {"success": False, "error": "content is required for replace; use remove to delete an entry."}
        threat = first_threat_id(content)
        if threat:
            return _threat_error(threat)
        path = self.path_for(target)
        with _file_lock(path):
            drift = self._detect_external_drift(target)
            if drift:
                return _drift_error(path, drift)
            entries = self.read_entries(target, create=True)
            match = _unique_substring_match(entries, old_text)
            if not match["success"]:
                return match
            next_entries = list(entries)
            next_entries[int(match["index"])] = content
            over = self._over_limit(target, next_entries)
            if over is not None:
                return self._overflow_error(target, entries, len(content), over)
            self._write_entries(path, next_entries)
            return self._success_response(target, next_entries, "Entry replaced.")

    def remove(self, target: str, old_text: str) -> dict[str, Any]:
        old_text = old_text.strip()
        if not old_text:
            return self._missing_old_text_error(target, "remove")
        path = self.path_for(target)
        with _file_lock(path):
            drift = self._detect_external_drift(target)
            if drift:
                return _drift_error(path, drift)
            entries = self.read_entries(target, create=True)
            match = _unique_substring_match(entries, old_text)
            if not match["success"]:
                return match
            next_entries = list(entries)
            next_entries.pop(int(match["index"]))
            self._write_entries(path, next_entries)
            return self._success_response(target, next_entries, "Entry removed.")

    def apply_batch(self, target: str, operations: list[Any]) -> dict[str, Any]:
        if not operations:
            return {"success": False, "error": "operations list is empty."}
        path = self.path_for(target)
        for index, raw in enumerate(operations, start=1):
            op = _mapping(raw)
            action = str(op.get("action") or "").strip()
            content = str(op.get("content") or "")
            if action in {"add", "replace"}:
                threat = first_threat_id(content)
                if threat:
                    return {"success": False, "error": f"Operation {index}: {_threat_error(threat)['error']}"}
        with _file_lock(path):
            drift = self._detect_external_drift(target)
            if drift:
                return _drift_error(path, drift)
            entries = self.read_entries(target, create=True)
            working = list(entries)
            for index, raw in enumerate(operations, start=1):
                op = _mapping(raw)
                action = str(op.get("action") or "").strip()
                content = str(op.get("content") or "").strip()
                old_text = str(op.get("old_text") or "").strip()
                label = f"Operation {index}"
                if action == "add":
                    if not content:
                        return self._batch_error(target, entries, f"{label}: content is required.")
                    if content not in working:
                        working.append(content)
                elif action == "replace":
                    if not old_text:
                        return self._batch_error(target, entries, f"{label}: old_text is required.")
                    if not content:
                        return self._batch_error(target, entries, f"{label}: content is required.")
                    match = _unique_substring_match(working, old_text)
                    if not match["success"]:
                        return self._batch_error(target, entries, f"{label}: {match['error']}")
                    working[int(match["index"])] = content
                elif action == "remove":
                    if not old_text:
                        return self._batch_error(target, entries, f"{label}: old_text is required.")
                    match = _unique_substring_match(working, old_text)
                    if not match["success"]:
                        return self._batch_error(target, entries, f"{label}: {match['error']}")
                    working.pop(int(match["index"]))
                else:
                    return self._batch_error(target, entries, f"{label}: unknown action. Use add, replace, or remove.")
            over = self._over_limit(target, working)
            if over is not None:
                return self._overflow_error(target, entries, 0, over)
            self._write_entries(path, working)
            return self._success_response(target, working, f"Applied {len(operations)} operation(s).")

    def path_for(self, target: str) -> Path:
        if target not in TARGET_FILENAMES:
            raise ValueError(f"invalid memory target: {target}")
        return self.storage_dir / TARGET_FILENAMES[target]

    def read_entries(self, target: str, *, create: bool = False) -> list[str]:
        path = self.path_for(target)
        if create:
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_text("", encoding="utf-8")
        if not path.exists():
            return []
        raw = path.read_text(encoding="utf-8")
        entries = [entry.strip() for entry in raw.split(ENTRY_DELIMITER)]
        deduped: list[str] = []
        seen: set[str] = set()
        for entry in entries:
            if not entry or entry in seen:
                continue
            seen.add(entry)
            deduped.append(entry)
        return deduped

    def render_block(self, target: str, entries: list[str]) -> str:
        if not entries:
            return ""
        content = ENTRY_DELIMITER.join(entries)
        limit = self._char_limit(target)
        current = len(content)
        pct = min(100, int((current / limit) * 100)) if limit > 0 else 0
        header = f"{TARGET_LABELS[target]} [{pct}% - {current:,}/{limit:,} chars]"
        separator = "=" * max(12, len(header))
        return f"{separator}\n{header}\n{separator}\n{content}"

    def _sanitize_entries_for_snapshot(self, entries: list[str], target: str) -> list[str]:
        filename = TARGET_FILENAMES[target]
        sanitized: list[str] = []
        for entry in entries:
            threat = first_threat_id(entry)
            if threat:
                sanitized.append(
                    f"[BLOCKED: {filename} entry contained threat pattern(s): {threat}. "
                    "Removed from system prompt; use memory(action=remove) to delete the original.]"
                )
            else:
                sanitized.append(entry)
        return sanitized

    def _write_entries(self, path: Path, entries: list[str]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_text_atomic(path, ENTRY_DELIMITER.join(entries) if entries else "")

    def _detect_external_drift(self, target: str) -> str | None:
        path = self.path_for(target)
        if not path.exists():
            return None
        raw = path.read_text(encoding="utf-8")
        if not raw.strip():
            return None
        parsed = [entry.strip() for entry in raw.split(ENTRY_DELIMITER) if entry.strip()]
        roundtrip = ENTRY_DELIMITER.join(parsed)
        max_entry = max((len(entry) for entry in parsed), default=0)
        if raw.strip() == roundtrip and max_entry <= self._char_limit(target):
            return None
        backup = path.with_suffix(path.suffix + f".bak.{int(time.time())}")
        backup.write_text(raw, encoding="utf-8")
        return str(backup)

    def _char_limit(self, target: str) -> int:
        return self.user_char_limit if target == "user" else self.memory_char_limit

    def _char_count(self, entries: list[str]) -> int:
        return len(ENTRY_DELIMITER.join(entries)) if entries else 0

    def _over_limit(self, target: str, entries: list[str]) -> int | None:
        count = self._char_count(entries)
        return count if count > self._char_limit(target) else None

    def _success_response(self, target: str, entries: list[str], message: str) -> dict[str, Any]:
        current = self._char_count(entries)
        limit = self._char_limit(target)
        pct = min(100, int((current / limit) * 100)) if limit > 0 else 0
        return {
            "success": True,
            "done": True,
            "target": target,
            "message": message,
            "usage": f"{pct}% - {current:,}/{limit:,} chars",
            "entry_count": len(entries),
            "note": "Write saved. This update is complete; do not repeat it.",
        }

    def _overflow_error(self, target: str, entries: list[str], added_chars: int, new_total: int) -> dict[str, Any]:
        current = self._char_count(entries)
        limit = self._char_limit(target)
        detail = f" Adding this entry ({added_chars} chars) would exceed the limit." if added_chars else ""
        return {
            "success": False,
            "error": f"Memory would be at {new_total:,}/{limit:,} chars.{detail} Remove or shorten stale entries and retry.",
            "current_entries": entries,
            "usage": f"{current:,}/{limit:,}",
        }

    def _missing_old_text_error(self, target: str, action: str) -> dict[str, Any]:
        entries = self.read_entries(target, create=True)
        return {
            "success": False,
            "error": f"old_text is required for {action}; use a short unique substring from current_entries.",
            "current_entries": entries,
            "usage": f"{self._char_count(entries):,}/{self._char_limit(target):,}",
        }

    def _batch_error(self, target: str, entries: list[str], message: str) -> dict[str, Any]:
        return {
            "success": False,
            "error": message + " No operations were applied.",
            "current_entries": entries,
            "usage": f"{self._char_count(entries):,}/{self._char_limit(target):,}",
        }


def first_threat_id(content: str) -> str | None:
    if any(char in content for char in _BIDI_CONTROLS):
        return "bidi_control"
    for pattern, threat_id in _THREAT_PATTERNS:
        if pattern.search(content):
            return threat_id
    return None


def tool_json_result(result: Mapping[str, Any]) -> str:
    return json.dumps(dict(result), ensure_ascii=False)


def _entry_count_label(count: int, label: str) -> str:
    suffix = "entry" if count == 1 else "entries"
    return f"{count} {label} {suffix}"


def tool_display_output(result: Mapping[str, Any]) -> str:
    if result.get("success"):
        if result.get("action") == "list":
            return str(result.get("message") or "memory listed")
        return str(result.get("message") or "memory updated")
    return str(result.get("error") or "memory update failed")


def _unique_substring_match(entries: list[str], old_text: str) -> dict[str, Any]:
    matches = [(index, entry) for index, entry in enumerate(entries) if old_text in entry]
    if not matches:
        return {"success": False, "error": f"No entry matched '{old_text}'."}
    if len({entry for _, entry in matches}) > 1:
        previews = [entry[:80] + ("..." if len(entry) > 80 else "") for _, entry in matches]
        return {
            "success": False,
            "error": f"Multiple entries matched '{old_text}'. Be more specific.",
            "matches": previews,
        }
    return {"success": True, "index": matches[0][0]}


def _threat_error(threat: str) -> dict[str, Any]:
    return {
        "success": False,
        "error": f"Memory content rejected: threat pattern '{threat}' is not allowed in system-prompt memory.",
    }


def _drift_error(path: Path, backup: str) -> dict[str, Any]:
    return {
        "success": False,
        "error": (
            f"Refusing to rewrite {path.name}: file content would not round-trip through the memory delimiter format. "
            f"A backup was saved to {backup}."
        ),
        "drift_backup": backup,
    }


@contextmanager
def _file_lock(path: Path):
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if fcntl is None and msvcrt is None:
        yield
        return
    with open(lock_path, "a+", encoding="utf-8") as lock_file:
        if fcntl is not None:
            fcntl.flock(lock_file, fcntl.LOCK_EX)
        else:  # pragma: no cover - Windows fallback
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(lock_file, fcntl.LOCK_UN)
            else:  # pragma: no cover - Windows fallback
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)


def _resolve_storage_dir(core_root: Path, storage: Mapping[str, Any]) -> Path:
    relative_to = str(storage.get("relative_to") or "core_root")
    if relative_to != "core_root":
        raise ValueError("memory_basic storage.relative_to only supports core_root")
    raw_path = str(storage.get("path") or "memory").strip()
    path = Path(raw_path)
    if path.is_absolute() or not raw_path or ".." in path.parts:
        raise ValueError("memory_basic storage.path must be a non-empty core-root-relative path")
    return (core_root / path).resolve()


def _write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), prefix=".mem_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
        _fsync_dir(path.parent)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _write_json_atomic(path: Path, data: Mapping[str, Any]) -> None:
    _write_text_atomic(path, json.dumps(dict(data), ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _load_json_mapping(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return dict(loaded) if isinstance(loaded, Mapping) else {}


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return dict(loaded) if isinstance(loaded, Mapping) else {}


def _deep_merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        current = result.get(key)
        if isinstance(current, Mapping) and isinstance(value, Mapping):
            result[str(key)] = _deep_merge(current, value)
        else:
            result[str(key)] = value
    return result


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _positive_int(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
