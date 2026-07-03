---
title: Runtime Layout Reference
description: Reference for source checkout and runtime-home file locations.
---

# Runtime Layout Reference

This page distinguishes the source checkout from the runtime home.

## Source Checkout

Typical repository checkout:

```text
demiurge-agent/
  demiurge/
  agents/
  package-repository/
  docs/
  website/
  ui-tui/
  scripts/
  tests/
```

Important source paths:

| Path | Owner / role |
| --- | --- |
| `demiurge/` | Python host package. |
| `agents/agent.yaml` | Source template for the global fallback config. |
| `agents/<core>/agent.yaml` | Source template for a concrete Agent Core. |
| `agents/<core>/agent/` | Source authored surface for that core. |
| `package-repository/` | Built-in package repository. |
| `docs/` | Source documentation. |

In source templates, `agents/agent.yaml` is not an Agent Core. Concrete cores
live under `agents/<core>/`.

## Runtime Home

Default runtime home:

```text
~/.demiurge/
```

Common children:

| Path | Owner / role |
| --- | --- |
| `config.yaml` | Host config, including default core, timezone, UI, providers, and package repositories. |
| `.env` | Local environment file loaded by the host. |
| `.core.git/` | Bare Git repository for the runtime agents tree. |
| `agents/agent.yaml` | Runtime global fallback config. |
| `agents/<core>/` | Active live checkout of a concrete Agent Core. |
| `.core-ignore` | Host-owned Git ignore file for runtime cache artifacts such as `__pycache__/`. |
| `.evolve/runs/<run_id>/agents/` | Isolated evolve worktree over the whole agents tree. |
| `runtime/runtime.sqlite3` | Runtime control-plane event store and projections. |
| `runtime/artifacts/` | Host-owned artifacts referenced by runtime records. |
| `runtime/session-events/` | Per-session diagnostic event logs. |
| `workspace/` | Default workspace when no CLI/env/core workspace is selected. |
| `logs/` | Runtime logs such as `mcp-stderr.log`. |

## Runtime Core

Active runtime core:

```text
~/.demiurge/agents/assistant/
  agent.yaml
  packages.yaml
  agent/
    SOUL.md
    pipelines.yaml
    bootstrap/
    input/
    output/
    tools/
    skills/
    schedules/
    mcp/
    lib/
```

The loader reads `agent.yaml`, resolves `runtime.surface_root`, then requires
`agent/pipelines.yaml` when the surface root is `agent`.

`packages.yaml` is package install state. Do not edit it manually unless you are
repairing package state with explicit direction.

## Authored Surface Defaults

With `runtime.surface_root: agent`, the defaults are:

| Surface | Default path |
| --- | --- |
| Core prompt | `agent/SOUL.md` |
| Pipelines | `agent/pipelines.yaml` |
| Bootstrap slots | `agent/bootstrap/` |
| Input slots | `agent/input/` |
| Output slots | `agent/output/` |
| Authored tools | `agent/tools/` when `slots.tools` points there |
| Skills | `agent/skills/` |
| Schedules | `agent/schedules/` |
| MCP servers | `agent/mcp/` |
| Shared authored helpers | `agent/lib/` |

Skills, schedules, and MCP roots are inferred from `runtime.surface_root` unless
configured. Bootstrap, input, and output are always resolved from
`runtime.surface_root`.

## Managed Checkout

Managed installs may keep a checkout under:

```text
~/.demiurge/demiurge-agent/
```

Runtime cores still live under `~/.demiurge/agents/`; the managed checkout is
not the active runtime core.

## Local Agent Edits

Direct changes under `~/.demiurge/agents/` are local agent edits. Demiurge saves
them as commits in `~/.demiurge/.core.git` before run/edit workflows load the
live core. The generated commit message is deterministic and includes changed
scopes, changed paths, detected semantic YAML changes, and gate status.

`demiurge core diff` is read-only. `demiurge core save` validates and commits
the current edits. `demiurge core discard --yes` resets the checkout to
`refs/demiurge/live` and removes untracked edits. Promotion and rollback do not
auto-save; they reject dirty live trees so a revision switch cannot silently
overwrite local files.

## Core Git Refs

Runtime core revisions are Git commits in `~/.demiurge/.core.git`.

| Ref | Meaning |
| --- | --- |
| `refs/demiurge/live` | Commit checked out at `~/.demiurge/agents/`. |
| `refs/demiurge/previous` | Default rollback target. |
| `refs/demiurge/runs/<run_id>` | Reviewed evolve proposal commit. |

`demiurge init` creates the repository from the source `agents/` tree on a
fresh runtime home. It does not migrate the old `registry/`, `history/`, or
`runs/` layouts.
