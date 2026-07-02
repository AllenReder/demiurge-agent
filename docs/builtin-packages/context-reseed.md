---
sidebar_position: 4
title: context_reseed
description: Install and use the built-in bounded continuity-note package.
---

# context_reseed

`context_reseed` keeps a bounded continuity note and injects it as reference
context at the start of future sessions.

Use it when you want lightweight handoff-style continuity without installing an
external memory provider.

## What It Installs

The package installs:

```text
agent/lib/context_reseed/
agent/bootstrap/context_reseed_bootstrap/
agent/output/context_reseed_output/
agent/skills/context_reseed/
```

It appends a bootstrap slot to the serial bootstrap pipeline and an output slot
to the serial output pipeline.

The note is stored outside package-owned component directories:

```text
context/reseed.md
```

Uninstall removes package-owned component directories and pipeline entries, but
leaves `context/reseed.md` in place.

## Install

Use the interactive manager:

```bash
uv run demiurge package
```

Or install with subcommands:

```bash
uv run demiurge package install context_reseed --core assistant --preview
uv run demiurge package install context_reseed --core assistant
```

## Options

| Option | Default | Description |
| --- | --- | --- |
| `mode` | `explicit` | `explicit` updates only when the user asks for a reseed, handoff, session, context, or continuity note. `auto` updates after each assistant output. |
| `max_chars` | `1800` | Maximum characters stored and injected from the note. |
| `notice` | `false` | Emits a transient output notice when the note is refreshed. |

Example:

```bash
uv run demiurge package install context_reseed \
  --core assistant \
  --option mode=auto \
  --option max_chars=2400
```

## Runtime Behavior

At session bootstrap, the bootstrap slot reads `context/reseed.md`, sanitizes
the note, bounds it to `max_chars`, quotes it as untrusted data, and injects it
as background reference context.

After assistant output, the output slot writes a new bounded note. In
`explicit` mode it writes only when the latest user input explicitly asks for
continuity, handoff, session, context, or reseed notes. In `auto` mode it writes
after every assistant output.

The package requires:

| Slot | Capability |
| --- | --- |
| Bootstrap | `fs.read` |
| Output | `fs.write` |

Both slots use `failure_policy: soft`.

## Safety Model

The stored note is treated as stale, untrusted reference data. Before injection,
the package strips bidirectional controls, redacts common credential patterns,
and blocks common prompt-injection phrases.

The generated note is not a source of system or developer instructions. Current
user input and higher-priority instructions still win.

## Verify

List installed packages:

```bash
uv run demiurge package list --core assistant
```

Ask for a continuity note:

```text
Please write a context reseed note for the next session.
```

Then inspect:

```text
~/.demiurge/agents/assistant/context/reseed.md
```

## Uninstall

```bash
uv run demiurge package uninstall context_reseed --core assistant --preview
uv run demiurge package uninstall context_reseed --core assistant
```

Uninstall removes package-owned files and pipeline entries. It does not remove
`context/reseed.md`.
