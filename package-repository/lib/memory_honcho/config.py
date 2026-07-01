from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping

import yaml


DEFAULT_CONFIG: dict[str, Any] = {
    "recall_mode": "hybrid",
    "enable_tools": True,
    "api_key": None,
    "api_key_env": "HONCHO_API_KEY",
    "base_url": None,
    "base_url_env": "HONCHO_BASE_URL",
    "workspace": "demiurge",
    "peer_name": None,
    "ai_peer": "demiurge-assistant",
    "session_strategy": "per-directory",
    "context_tokens": 1200,
    "timeout_seconds": 3,
    "context_cadence": 1,
    "storage": {
        "relative_to": "core_root",
        "path": "memory/honcho",
    },
}

_RECALL_MODES = {"hybrid", "context", "tools"}
_SESSION_STRATEGIES = {"per-directory", "per-repo", "per-session", "global"}


def load_config(slot_file: str | Path) -> dict[str, Any]:
    core_root = resolve_core_root(slot_file)
    config = _deep_merge(DEFAULT_CONFIG, _load_yaml_mapping(core_root / "agent" / "lib" / "memory_honcho" / "config.yaml"))
    config = _deep_merge(config, _load_yaml_mapping(Path(slot_file).with_name("config.yaml")))
    config["core_root"] = str(core_root)
    config["storage_dir"] = str(_resolve_storage_dir(core_root, _mapping(config.get("storage"))))
    config["recall_mode"] = _choice(config.get("recall_mode"), _RECALL_MODES, "hybrid")
    config["session_strategy"] = _choice(config.get("session_strategy"), _SESSION_STRATEGIES, "per-directory")
    config["enable_tools"] = _bool(config.get("enable_tools"), default=True)
    config["context_tokens"] = _positive_int(config.get("context_tokens"), 1200)
    config["timeout_seconds"] = _positive_int(config.get("timeout_seconds"), 3)
    config["context_cadence"] = _positive_int(config.get("context_cadence"), 1)
    return config


def resolve_core_root(slot_file: str | Path) -> Path:
    path = Path(slot_file).resolve()
    start = path if path.is_dir() else path.parent
    for candidate in [start, *start.parents]:
        if (candidate / "agent.yaml").exists() and (candidate / "agent").is_dir():
            return candidate
    raise ValueError(f"could not resolve core root from slot path: {slot_file}")


def credential(config: Mapping[str, Any], key: str, env_key: str) -> str:
    value = config.get(key)
    if value is not None and str(value).strip():
        return str(value).strip()
    env_name = str(config.get(env_key) or "").strip()
    if env_name:
        return os.environ.get(env_name, "").strip()
    return ""


def _resolve_storage_dir(core_root: Path, storage: Mapping[str, Any]) -> Path:
    relative_to = str(storage.get("relative_to") or "core_root")
    if relative_to != "core_root":
        raise ValueError("memory_honcho storage.relative_to only supports core_root")
    raw_path = str(storage.get("path") or "memory/honcho").strip()
    path = Path(raw_path)
    if path.is_absolute() or not raw_path or ".." in path.parts:
        raise ValueError("memory_honcho storage.path must be a non-empty core-root-relative path")
    resolved = (core_root / path).resolve()
    if not _is_relative_to(resolved, core_root.resolve()):
        raise ValueError("memory_honcho storage.path must resolve inside the core root")
    return resolved


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return dict(loaded) if isinstance(loaded, Mapping) else {}


def _deep_merge(base: Mapping[str, Any], update: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in update.items():
        current = result.get(str(key))
        if isinstance(current, Mapping) and isinstance(value, Mapping):
            result[str(key)] = _deep_merge(current, value)
        else:
            result[str(key)] = value
    return result


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _choice(value: Any, allowed: set[str], default: str) -> str:
    text = str(value or default).strip()
    return text if text in allowed else default


def _bool(value: Any, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "enabled"}:
        return True
    if text in {"0", "false", "no", "off", "disabled"}:
        return False
    return default


def _positive_int(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False
