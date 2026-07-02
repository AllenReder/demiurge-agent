---
title: Runtime Layout Reference
description: Reference for source checkout and runtime-home file locations.
---

# Runtime Layout Reference

## Source Checkout

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

`demiurge/` is the Python package. `agents/` contains source Agent Core
templates. `package-repository/` contains the built-in package repository.

## Runtime Home

Default:

```text
~/.demiurge/
```

Common children:

| Path | Owner |
| --- | --- |
| `config.yaml` | Host config. |
| `.env` | Local secret environment file. |
| `agents/` | Runtime Agent Cores. |
| `runtime/runtime.sqlite3` | Runtime control-plane event store and projections. |
| `runtime/artifacts/` | Host-owned artifacts referenced by SQLite artifact rows. |
| `runtime/session-events/` | Per-session diagnostic event logs. |
| `workspace/` | Non-local fallback workspace. |
| `logs/` | Runtime logs such as MCP stderr logs. |

## Runtime Core

```text
~/.demiurge/agents/assistant/
  agent.yaml
  packages.yaml
  agent/
    SOUL.md
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

## Package Repository

```text
package-repository/
  repository.yaml
  packages/
  bootstrap/
  input/
  output/
  tool/
  skill/
  lib/
  core/
```

## Managed Checkout

Managed install uses:

```text
~/.demiurge/demiurge-agent/
```

Runtime cores stay under `~/.demiurge/agents/`.
