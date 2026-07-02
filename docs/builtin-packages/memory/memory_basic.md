---
sidebar_position: 1
title: memory_basic
description: Install and use the built-in file-backed memory package.
---

# memory_basic

`memory_basic` adds local persistent memory files to an Agent Core. It is the
smallest built-in memory package: no external service, no network dependency,
and no Python dependency outside the locked Demiurge environment.

Use it when you want simple Hermes-style `USER.md` and `MEMORY.md` recall.

## What It Installs

The package installs:

```text
agent/lib/memory_basic/
agent/bootstrap/memory_basic/
agent/tools/memory/
```

It also edits `agent/slots.yaml`:

```yaml
pipelines:
  bootstrap:
    serial:
      - memory_basic
```

If the core already has the default `session_context` bootstrap slot, the
installer inserts `memory_basic` after it.

The durable memory files live outside package-owned component directories:

```text
memory/
  MEMORY.md
  USER.md
```

Uninstall removes package-owned lib, bootstrap, and tool files, but leaves the
`memory/` data directory in place.

## Install

Preview first:

```bash
uv run demiurge package install memory_basic --core assistant --preview
```

Install:

```bash
uv run demiurge package install memory_basic --core assistant
```

## Runtime Behavior

At session bootstrap, `memory_basic` reads `memory/MEMORY.md` and
`memory/USER.md` and injects a frozen memory snapshot into the host bootstrap
context. That snapshot is reused for the session. Writes made during the same
session are visible to the `memory` tool, but they are not injected into the
model prompt until a new session starts.

The default character budgets are:

| Store | Default limit |
| --- | --- |
| `MEMORY.md` | 2200 chars |
| `USER.md` | 1375 chars |

## Tool

The package installs one authored tool:

| Tool | Approval | Use |
| --- | --- | --- |
| `memory` | `auto` | Add, replace, remove, or list entries in `MEMORY.md` and `USER.md`. |

Use `target=memory` for project conventions, environment facts, and workflow
lessons. Use `target=user` for stable user profile or preference facts. Use
`target=all` only with `action=list`.

The tool supports single operations:

```json
{"target": "memory", "action": "add", "content": "Use uv for Python commands."}
```

and batch operations:

```json
{
  "target": "memory",
  "operations": [
    {"action": "remove", "old_text": "old convention"},
    {"action": "add", "content": "new convention"}
  ]
}
```

Batch writes are all-or-nothing against the final character budget.

## Verify

List installed packages:

```bash
uv run demiurge package list --core assistant
```

After a memory write, inspect:

```text
~/.demiurge/agents/assistant/memory/MEMORY.md
~/.demiurge/agents/assistant/memory/USER.md
```

## Uninstall

```bash
uv run demiurge package uninstall memory_basic --core assistant --preview
uv run demiurge package uninstall memory_basic --core assistant
```

Uninstall restores the bootstrap pipeline and removes package-owned component
directories. It does not remove `memory/MEMORY.md` or `memory/USER.md`.

## When To Use memory_honcho Instead

Use [`memory_honcho`](memory_honcho.md) when you want Honcho-backed
cross-session modeling, automatic remote recall, completed-turn sync, or
explicit `honcho_*` tools. Use `memory_basic` when local file-backed memory is
enough.
