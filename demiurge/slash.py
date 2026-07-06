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
    SlashCommandSpec("help", "Show command groups", "Core", surfaces=("tui", "telegram", "text")),
    SlashCommandSpec("status", "Show runtime status", "Core", surfaces=("tui", "telegram", "text")),
    SlashCommandSpec("core", "Show active core", "Core"),
    SlashCommandSpec("versions", "List core revisions", "Core"),
    SlashCommandSpec("provider", "Show provider", "Core"),
    SlashCommandSpec("doctor", "Check runtime/source drift", "Core"),
    SlashCommandSpec("tools", "Show visible tools", "Tools", surfaces=("tui", "telegram", "text")),
    SlashCommandSpec("skills", "Show skill index", "Tools", "/skills [category]", surfaces=("tui", "telegram", "text")),
    SlashCommandSpec("skill", "View a skill or linked file", "Tools", "/skill <name> [file_path]", surfaces=("tui", "telegram", "text")),
    SlashCommandSpec("packages", "List or install agent packages", "Tools", "/packages [package|install <package>|uninstall <package>]"),
    SlashCommandSpec("tool-display", "Show or change tool display", "Tools", "/tool-display quiet|summary|full"),
    SlashCommandSpec("sessions", "List recent sessions", "Sessions", "/sessions [limit]", surfaces=("tui", "telegram", "text")),
    SlashCommandSpec("subagents", "List, inspect, or cancel child agent tasks", "Sessions", "/subagents [task_id|cancel <task_id>]", surfaces=("tui", "telegram")),
    SlashCommandSpec(
        "resume",
        "Resume by id or numbered list entry",
        "Sessions",
        "/resume [session_id|number]",
        surfaces=("tui", "telegram", "text"),
    ),
    SlashCommandSpec("new", "Start a new session", "Sessions", surfaces=("tui", "telegram", "text")),
    SlashCommandSpec("compact", "Compact older session history", "Sessions", "/compact [focus]"),
    SlashCommandSpec("last", "Show previous turn trace", "Trace"),
    SlashCommandSpec("trace", "Show a turn trace", "Trace", "/trace [turn_id|last]"),
    SlashCommandSpec("events", "Show recent events", "Trace", "/events [type] [limit]"),
    SlashCommandSpec("busy", "Set running-input behavior", "Control", "/busy interrupt|queue", surfaces=("tui", "telegram", "text")),
    SlashCommandSpec("interrupt", "Soft-cancel the current turn", "Control"),
    SlashCommandSpec("stop", "Stop the current turn and clear queued input", "Control", surfaces=("telegram", "text")),
    SlashCommandSpec("queue", "Queue a prompt for the next turn", "Control", "/queue <prompt>", surfaces=("telegram", "text")),
    SlashCommandSpec("evolve", "Manage evolve runs", "Control", "/evolve <goal>|review <run_id>|promote <run_id>|discard <run_id>"),
    SlashCommandSpec("rollback", "Create a rollback commit for the next turn", "Control", "/rollback [target]"),
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


def command_names_for_surface(surface: str, specs: Iterable[SlashCommandSpec] = SLASH_COMMANDS) -> frozenset[str]:
    return frozenset(spec.name for spec in specs_for_surface(surface, specs))


def help_text_for_surface(
    surface: str,
    specs: Iterable[SlashCommandSpec] = SLASH_COMMANDS,
    *,
    extra_lines: Iterable[str] = (),
    footer_lines: Iterable[str] = (),
) -> str:
    lines = ["# Commands"]
    current_group = ""
    for spec in specs_for_surface(surface, specs):
        if spec.group != current_group:
            current_group = spec.group
            lines.extend(["", f"## {current_group}"])
        usage = spec.usage or f"/{spec.name}"
        lines.append(f"- `{usage}` - {spec.description}")
    extra = tuple(extra_lines)
    if extra:
        lines.extend(["", *extra])
    footer = tuple(footer_lines)
    if footer:
        lines.extend(["", *footer])
    return "\n".join(lines)


def telegram_command_specs(specs: Iterable[SlashCommandSpec] = SLASH_COMMANDS) -> tuple[SlashCommandSpec, ...]:
    return tuple(spec for spec in specs_for_surface("telegram", specs) if _TELEGRAM_COMMAND_RE.fullmatch(spec.name))
