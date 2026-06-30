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
  input/
  output/
  tool/
  skill/
  lib/
  core/
  packages/
```

Supported component kinds:

- `input`
- `output`
- `tool`
- `skill`
- `lib`
- `core`

Package recipes select components, collect options, write component config, and
optionally edit input/output pipelines.

## Built-In Packages

`memory_basic` installs:

- `agent/lib/memory_basic`
- `agent/input/memory_context`
- `agent/tools/memory`

It stores user data outside package-owned component targets:

```text
~/.demiurge/agents/assistant/memory/
  USER.md
  MEMORY.md
```

`minimax_tts` installs TTS output/tool components and may install a summarizer
core depending on options.

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
