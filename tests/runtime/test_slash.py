from __future__ import annotations

from demiurge.slash import (
    SlashCommandSpec,
    command_names_for_surface,
    help_text_for_surface,
    telegram_command_specs,
)


def test_text_surface_command_names_match_existing_text_channel_commands():
    assert command_names_for_surface("text") == frozenset(
        {"help", "status", "new", "stop", "queue", "busy", "sessions", "resume", "tools", "skills", "skill"}
    )


def test_tui_help_text_uses_tui_surface_only():
    text = help_text_for_surface("tui", footer_lines=("Enter submits. Ctrl-C interrupts a running turn.",))

    assert "# Commands" in text
    assert "`/interrupt`" in text
    assert "`/tool-display quiet|summary|full`" in text
    assert "`/stop`" not in text
    assert "`/queue <prompt>`" not in text
    assert "Enter submits. Ctrl-C interrupts a running turn." in text


def test_text_help_can_include_ask_without_dispatching_ask():
    text = help_text_for_surface("text", extra_lines=("- `/ask <prompt>` - send a prompt",))

    assert "`/ask <prompt>`" in text
    assert "ask" not in command_names_for_surface("text")


def test_telegram_command_specs_filter_surface_and_bot_command_names():
    specs = (
        SlashCommandSpec("ok", "OK", "Test", surfaces=("telegram",)),
        SlashCommandSpec("bad-name", "Bad", "Test", surfaces=("telegram",)),
        SlashCommandSpec("tui_only", "TUI only", "Test", surfaces=("tui",)),
    )

    assert [spec.name for spec in telegram_command_specs(specs)] == ["ok"]
