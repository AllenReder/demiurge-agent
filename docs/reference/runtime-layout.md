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
| `agents/agent.yaml` | Runtime global fallback config. |
| `agents/<core>/` | Active runtime copy of a concrete Agent Core. |
| `registry/<core>.json` | Active pointer for each core. |
| `history/<core>/` | Backups of previous active core versions. |
| `history/_global/` | Backups of the global fallback config. |
| `runs/<core>/<run_id>/candidate/` | Candidate core workspaces created by evolution. |
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
    tests/
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
| Core-local tests | `agent/tests/` |

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
