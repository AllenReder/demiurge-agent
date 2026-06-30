# Packages

Packages install reusable catalog components into a runtime agent core. They
are a user-facing workflow for composing cores from input modules, output
modules, tools, skills, shared libraries, or child cores.

## Install a Package

Use the interactive wizard:

```bash
uv run demiurge package
```

Scripted install:

```bash
uv run demiurge package list --core assistant
uv run demiurge package install memory_basic --core assistant --preview
uv run demiurge package install memory_basic --core assistant
```

Uninstall:

```bash
uv run demiurge package uninstall memory_basic --core assistant --preview
uv run demiurge package uninstall memory_basic --core assistant
```

## What Changes

Package install modifies only the target runtime core, for example:

```text
~/.demiurge/agents/assistant/
```

It does not modify repository source templates under `agents/`.

Each target core stores `packages.yaml` at its root. Component configuration
lives in each installed component's `config.yaml`.

## Catalog Components

The built-in catalog lives in:

```text
agent-catalog/
  catalog.yaml
  bootstrap/  # when bootstrap components are present
  input/
  output/
  tool/
  skill/
  lib/
  core/
  packages/
```

Supported component kinds:

- `bootstrap`
- `input`
- `output`
- `tool`
- `skill`
- `lib`
- `core`

Package recipes select components, collect options, write component config, and
optionally edit bootstrap/input/output pipelines. Bootstrap pipelines are
serial-only.

## Built-In Packages

`memory_basic` installs:

- `agent/lib/memory_basic`
- `agent/bootstrap/memory_basic`
- `agent/tools/memory`

It stores user data outside package-owned component targets:

```text
~/.demiurge/agents/assistant/memory/
  USER.md
  MEMORY.md
```

`conversation_style` installs:

- `agent/input/conversation_style`
- `agent/skills/conversation_style`

It injects transient per-turn response style hints and can auto-load the packaged
style skill. Options choose `concise`, `balanced`, `detailed`, or `technical`
style, plus channel-aware hints.

`context_reseed` installs:

- `agent/lib/context_reseed`
- `agent/bootstrap/context_reseed`
- `agent/output/context_reseed`
- `agent/skills/context_reseed`

It writes a bounded continuity note outside package-owned component targets when
explicitly requested by default, and injects that note as quoted, reference-only
bootstrap context in future sessions:

```text
~/.demiurge/agents/assistant/context/reseed.md
```

`minimax_tts` installs `agent/lib/tts_minimax` and
`agent/output/tts_minimax` by default. `enable_tool=true` adds
`agent/tools/text_to_speech` and `agent/skills/tts_voice`; `mode=summary` also
installs the `tts_summarizer` child core.

## Success Check

```bash
uv run demiurge package list --core assistant
uv run demiurge init --check
uv run demiurge --provider fake
```

Use `/tools` after installing a tool package.

## Boundary

Package management is not an agent-callable model tool. It is a CLI/TUI helper
for user-controlled runtime core edits. It does not manage dependency changes.
