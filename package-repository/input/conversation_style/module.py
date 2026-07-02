from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import yaml


STYLE_HINTS = {
    "concise": "Prefer concise, scannable answers. Lead with the result, then include only the context needed to act safely.",
    "balanced": "Prefer a balanced answer: direct result first, then relevant reasoning, caveats, and next steps.",
    "detailed": "Prefer detailed answers with enough context for the user to understand trade-offs and reproduce the work.",
    "technical": "Prefer precise technical language, explicit file/command references, assumptions, and verification details.",
}

CHANNEL_HINTS = {
    "telegram": "The user is on Telegram; keep paragraphs short and avoid wide tables unless necessary.",
    "tui": "The user is in a terminal UI; use terminal-friendly Markdown and avoid unnecessary visual clutter.",
}


def process(ctx):
    config = load_config(__file__)
    style = str(config.get("style") or "balanced").strip().lower()
    style_hint = STYLE_HINTS.get(style, STYLE_HINTS["balanced"])
    parts = [
        "Conversation style package hint (lower priority than system, developer, and explicit user instructions):",
        f"- {style_hint}",
    ]

    if _config_bool(config.get("channel_hint"), default=True):
        channel = str(ctx.turn.metadata.get("channel") or "").strip().lower()
        channel_hint = CHANNEL_HINTS.get(channel)
        if channel_hint:
            parts.append(f"- {channel_hint}")

    ctx.input.add_context("\n".join(parts), role="system", write_history=False)

    if _config_bool(config.get("activate_skill"), default=True):
        ctx.skills.activate("conversation_style")


def load_config(slot_file: str | Path) -> dict[str, Any]:
    core_root = resolve_core_root(slot_file)
    config = _load_required_yaml_mapping(Path(slot_file).with_name("config.yaml"))
    config["core_root"] = str(core_root)
    return config


def resolve_core_root(slot_file: str | Path) -> Path:
    path = Path(slot_file).resolve()
    start = path if path.is_dir() else path.parent
    for candidate in [start, *start.parents]:
        if (candidate / "agent.yaml").exists() and (candidate / "agent").is_dir():
            return candidate
    raise ValueError(f"could not resolve core root from slot path: {slot_file}")


def _load_required_yaml_mapping(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ValueError(f"required conversation_style config not found: {path}")
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, Mapping):
        raise ValueError(f"conversation_style config must be a mapping: {path}")
    return dict(loaded)


def _config_bool(value: Any, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on", "enabled"}:
            return True
        if normalized in {"0", "false", "no", "off", "disabled"}:
            return False
    return default
