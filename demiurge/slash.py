from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


@dataclass(slots=True)
class SlashCommand:
    name: str
    args: str = ""


@dataclass(frozen=True, slots=True)
class SlashCommandSpec:
    name: str
    description: str
    group: str
    usage: str | None = None
    surfaces: tuple[str, ...] = ("tui",)


SLASH_COMMANDS: tuple[SlashCommandSpec, ...] = (
    SlashCommandSpec("help", "Show command groups", "Core", surfaces=("tui", "telegram")),
    SlashCommandSpec("status", "Show runtime status", "Core", surfaces=("tui", "telegram")),
    SlashCommandSpec("core", "Show active core", "Core"),
    SlashCommandSpec("versions", "List core versions", "Core"),
    SlashCommandSpec("provider", "Show provider", "Core"),
    SlashCommandSpec("doctor", "Check runtime/source drift", "Core"),
    SlashCommandSpec("tools", "Show visible tools", "Tools", surfaces=("tui", "telegram")),
    SlashCommandSpec("skills", "Show skill index", "Tools", "/skills [category]", surfaces=("tui", "telegram")),
    SlashCommandSpec("skill", "View a skill or linked file", "Tools", "/skill <name> [file_path]", surfaces=("tui", "telegram")),
    SlashCommandSpec("packages", "List or install agent packages", "Tools", "/packages [package|install <package>|uninstall <package>]"),
    SlashCommandSpec("tool-display", "Show or change tool display", "Tools", "/tool-display quiet|summary|full"),
    SlashCommandSpec("sessions", "List recent sessions", "Sessions", "/sessions [limit]", surfaces=("tui", "telegram")),
    SlashCommandSpec(
        "resume",
        "Resume by id or numbered list entry",
        "Sessions",
        "/resume [session_id|number]",
        surfaces=("tui", "telegram"),
    ),
    SlashCommandSpec("new", "Start a new session", "Sessions", surfaces=("tui", "telegram")),
    SlashCommandSpec("compact", "Compact older session history", "Sessions", "/compact [focus]"),
    SlashCommandSpec("last", "Show previous turn trace", "Trace"),
    SlashCommandSpec("trace", "Show a turn trace", "Trace", "/trace [turn_id|last]"),
    SlashCommandSpec("events", "Show recent events", "Trace", "/events [type] [limit]"),
    SlashCommandSpec("busy", "Set running-input behavior", "Control", "/busy interrupt|queue", surfaces=("tui", "telegram")),
    SlashCommandSpec("interrupt", "Soft-cancel the current turn", "Control"),
    SlashCommandSpec("stop", "Stop the current turn and clear queued input", "Control", surfaces=("telegram",)),
    SlashCommandSpec("queue", "Queue a prompt for the next turn", "Control", "/queue <prompt>", surfaces=("telegram",)),
    SlashCommandSpec("evolve", "Create and gate a candidate core", "Control", "/evolve <goal>"),
    SlashCommandSpec("rollback", "Switch active pointer next turn", "Control", "/rollback [version]"),
    SlashCommandSpec("exit", "Quit", "Control"),
    SlashCommandSpec("quit", "Quit", "Control"),
)

_TELEGRAM_COMMAND_RE = re.compile(r"^[a-z0-9_]{1,32}$")


def parse_slash_command(text: str) -> SlashCommand | None:
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None
    name, _, args = stripped[1:].partition(" ")
    name = name.split("@", 1)[0].lower()
    if not name:
        return None
    return SlashCommand(name=name, args=args.strip())


def specs_for_surface(surface: str, specs: Iterable[SlashCommandSpec] = SLASH_COMMANDS) -> tuple[SlashCommandSpec, ...]:
    return tuple(spec for spec in specs if surface in spec.surfaces)


def telegram_command_specs(specs: Iterable[SlashCommandSpec] = SLASH_COMMANDS) -> tuple[SlashCommandSpec, ...]:
    return tuple(spec for spec in specs_for_surface("telegram", specs) if _TELEGRAM_COMMAND_RE.fullmatch(spec.name))
