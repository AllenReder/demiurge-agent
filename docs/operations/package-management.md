# Package Management

Package management is a user-controlled workflow for installing reusable
catalog packages into runtime cores.

## Interactive Wizard

```bash
uv run demiurge package
```

The wizard selects a target core, searches or browses packages, collects
options, shows a preview, then asks for confirmation.

## Scripted Commands

```bash
uv run demiurge package list --core assistant
uv run demiurge package list --tag tts --json
uv run demiurge package install minimax_tts --core assistant --preview
uv run demiurge package install minimax_tts --core assistant --option mode=summary
uv run demiurge package uninstall minimax_tts --core assistant --preview
```

The package command supports list, install, and uninstall. It does not support
reinstall, config edit, upgrade, rollback, git commits, or agent-callable
package management.

## Built-In Catalog Highlights

The built-in catalog includes reusable examples for the main agent-core slot
kinds:

- `memory_basic`: bootstrap + tool + shared lib for durable local memory.
- `conversation_style`: input + skill package for per-turn communication hints.
- `context_reseed`: output + bootstrap + skill + shared lib for bounded continuity
  notes across sessions, saved only when explicitly requested by default.
- `minimax_tts`: shared lib + output + optional tool/skill/core package for speech
  artifacts.

These packages are optional runtime-core overlays. They showcase composable
agent-core modules without changing source templates or installing host Python
dependencies.

## Runtime State

Install writes to the target runtime core and records package ownership in:

```text
~/.demiurge/agents/<core>/packages.yaml
```

Uninstall uses this registry to remove owned components and pipeline entries.
Shared reused targets are kept until the final referencing package is removed.

## Success Check

```bash
uv run demiurge package list --core assistant
uv run demiurge --provider fake
```

Use `/tools` if the package installed an authored tool.

## Reference

See [../authoring/packages.md](../authoring/packages.md) for how packages
compose agent cores and [../reference/package-recipes.md](../reference/package-recipes.md)
for recipe fields.
