# Package Management

Package management is a user-controlled workflow for installing reusable
package repository packages into runtime cores.

## Interactive Wizard

```bash
uv run demiurge package
```

The wizard selects a target core, manages package repositories, searches or
browses packages, collects options, shows a preview, then asks for confirmation.

## Scripted Commands

```bash
uv run demiurge package list --core assistant
uv run demiurge package list --repo builtin
uv run demiurge package list --tag tts --json
uv run demiurge package install minimax_tts --core assistant --preview
uv run demiurge package install builtin/minimax_tts --core assistant --preview
uv run demiurge package install minimax_tts --core assistant --option mode=summary
uv run demiurge package uninstall minimax_tts --core assistant --preview
```

Repository management:

```bash
uv run demiurge package repo list
uv run demiurge package repo add community https://github.com/user/demiurge-packages.git --trust
uv run demiurge package repo sync community
uv run demiurge package repo remove community
```

External `path` and `git` repositories must be explicitly trusted because their
packages can install local code slots into the host-shared runtime environment.
`repo sync` updates repository caches only; it does not update already installed
packages.

The package command does not support reinstall, package update, config edit,
rollback, git commits, or agent-callable package management.

## Built-In Repository Highlights

The built-in repository includes reusable examples for the main agent-core slot
kinds:

- `memory_basic`: bootstrap + tool + shared lib for durable local memory.
- `conversation_style`: input + skill package for per-turn communication hints.
- `context_reseed`: output + bootstrap + skill + shared lib for bounded continuity
  notes across sessions, saved only when explicitly requested by default.
- `minimax_tts`, `tts_openai`, `tts_xai`, and `tts_gemini`: shared lib + output
  + optional provider-specific tool/skill/core package for speech artifacts.

These packages are optional runtime-core overlays. They showcase composable
agent-core modules without changing source templates or installing host Python
dependencies. For provider packages, prefer API-key environment variables for
non-plaintext storage; direct `api_key` package-option values are written into
the installed runtime component's `config.yaml`.

## Runtime State

Install writes to the target runtime core and records package ownership in:

```text
~/.demiurge/agents/<core>/packages.yaml
```

Uninstall uses this registry to remove owned components and pipeline entries.
Shared reused targets are kept until the final referencing package is removed.

Git repository caches live under:

```text
~/.demiurge/package-repositories/<alias>/
```

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
