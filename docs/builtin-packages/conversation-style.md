---
sidebar_position: 5
title: conversation_style
description: Install configurable per-turn conversation style hints and the matching style skill.
---

# conversation_style

`conversation_style` adds configurable communication style hints before each
model request. It can also activate a packaged skill that reinforces the same
style preference.

Use it when a core should consistently prefer concise, balanced, detailed, or
technical responses without rewriting the core's main prompt.

## What It Installs

The package installs:

```text
agent/input/conversation_style/
agent/skills/conversation_style/
```

It appends the input slot to the serial input pipeline.

## Install

Use the interactive manager:

```bash
uv run demiurge package
```

Or install with subcommands:

```bash
uv run demiurge package install conversation_style --core assistant --preview
uv run demiurge package install conversation_style --core assistant
```

Install a technical style:

```bash
uv run demiurge package install conversation_style \
  --core assistant \
  --option style=technical
```

## Options

| Option | Default | Description |
| --- | --- | --- |
| `style` | `balanced` | Reply style. Choices: `concise`, `balanced`, `detailed`, `technical`. |
| `channel_hint` | `true` | Adds lightweight Telegram or TUI formatting hints when channel metadata is present. |
| `activate_skill` | `true` | Activates the packaged `conversation_style` skill for each turn. |

Style modes:

| Mode | Behavior |
| --- | --- |
| `concise` | Short, scannable answers with only necessary context. |
| `balanced` | Direct result plus relevant reasoning, caveats, and next steps. |
| `detailed` | More explanation, trade-offs, and reproducible details. |
| `technical` | Precise technical language, explicit references, assumptions, and verification. |

## Runtime Behavior

The input slot injects a low-priority system context hint before each model
request. The hint is lower priority than system instructions, developer
instructions, and the latest user request.

When `channel_hint=true`, the slot adds a short channel-specific formatting
hint for known channels such as Telegram or the TUI.

When `activate_skill=true`, the slot requires:

```text
skill.activate:conversation_style
```

The activated skill tells the model to treat style as a preference, not a policy
override.

## Verify

List installed packages:

```bash
uv run demiurge package list --core assistant
```

Run a turn and inspect the response style. If the latest user request asks for a
different level of detail, the latest user request should take precedence.

## Uninstall

```bash
uv run demiurge package uninstall conversation_style --core assistant --preview
uv run demiurge package uninstall conversation_style --core assistant
```

Uninstall removes the package-owned input slot, skill, and pipeline entry.
