---
title: Runtime Layout 参考
description: Source checkout 和 runtime-home 文件位置的参考。
---

# Runtime Layout 参考

本页区分 source checkout 和 runtime home。

## Source Checkout

典型 repository checkout：

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

重要 source paths：

| Path | Owner / role |
| --- | --- |
| `demiurge/` | Python host package。 |
| `agents/agent.yaml` | 全局 fallback config 的 source template。 |
| `agents/<core>/agent.yaml` | 具体 Agent Core 的 source template。 |
| `agents/<core>/agent/` | 该 core 的 source authored surface。 |
| `package-repository/` | Built-in package repository。 |
| `docs/` | Source documentation。 |

在 source templates 中，`agents/agent.yaml` 不是 Agent Core。具体 cores 位于 `agents/<core>/` 下。

## Runtime Home

默认 runtime home：

```text
~/.demiurge/
```

常见子项：

| Path | Owner / role |
| --- | --- |
| `config.yaml` | Host config，包括 default core、timezone、UI、providers 和 package repositories。 |
| `.env` | Host 加载的 local environment file。 |
| `agents/agent.yaml` | Runtime global fallback config。 |
| `agents/<core>/` | 具体 Agent Core 的 active runtime copy。 |
| `registry/<core>.json` | 每个 core 的 active pointer。 |
| `history/<core>/` | 之前 active core versions 的 backups。 |
| `history/_global/` | Global fallback config 的 backups。 |
| `runs/<core>/<run_id>/candidate/` | Evolution 创建的 candidate core workspaces。 |
| `runtime/runtime.sqlite3` | Runtime control-plane event store 和 projections。 |
| `runtime/artifacts/` | Runtime records 引用的 host-owned artifacts。 |
| `runtime/session-events/` | Per-session diagnostic event logs。 |
| `workspace/` | 未选择 CLI/env/core workspace 时使用的默认 workspace。 |
| `logs/` | Runtime logs，例如 `mcp-stderr.log`。 |

## Runtime Core

Active runtime core：

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

Loader 会读取 `agent.yaml`，解析 `runtime.surface_root`，然后当 surface root 是 `agent` 时要求 `agent/pipelines.yaml`。

`packages.yaml` 是 package install state。除非你在明确指示下修复 package state，否则不要手动编辑它。

## Authored Surface Defaults

使用 `runtime.surface_root: agent` 时，默认值是：

| Surface | Default path |
| --- | --- |
| Core prompt | `agent/SOUL.md` |
| Pipelines | `agent/pipelines.yaml` |
| Bootstrap slots | `agent/bootstrap/` |
| Input slots | `agent/input/` |
| Output slots | `agent/output/` |
| Authored tools | 当 `slots.tools` 指向此处时为 `agent/tools/` |
| Skills | `agent/skills/` |
| Schedules | `agent/schedules/` |
| MCP servers | `agent/mcp/` |
| Shared authored helpers | `agent/lib/` |
| Core-local tests | `agent/tests/` |

Skills、schedules 和 MCP roots 会从 `runtime.surface_root` 推断，除非另有配置。Bootstrap、input 和 output 始终从 `runtime.surface_root` 解析。

## Managed Checkout

Managed installs 可能把 checkout 放在：

```text
~/.demiurge/demiurge-agent/
```

Runtime cores 仍位于 `~/.demiurge/agents/` 下；managed checkout 不是 active runtime core。
