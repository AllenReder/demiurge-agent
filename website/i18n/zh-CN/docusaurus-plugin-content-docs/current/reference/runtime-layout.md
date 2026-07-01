---
title: Runtime Layout 参考
description: source checkout 和 runtime-home file locations 的参考说明。
---

# Runtime Layout 参考

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

`demiurge/` 是 Python package。`agents/` 包含 source Agent Core templates。`package-repository/` 包含内置 package repository。

## Runtime Home

默认值：

```text
~/.demiurge/
```

常见子项：

| Path | Owner |
| --- | --- |
| `config.yaml` | Host config. |
| `.env` | 本地 secret environment file. |
| `agents/` | Runtime Agent Cores. |
| `sessions/` | Session records. |
| `scheduler/` | Scheduler state 和 run records. |
| `workspace/` | Non-local fallback workspace. |
| `logs/` | Runtime logs，例如 MCP stderr logs。 |

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

managed install 使用：

```text
~/.demiurge/demiurge-agent/
```

Runtime cores 仍保留在 `~/.demiurge/agents/` 下。
