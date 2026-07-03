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
| `.core.git/` | Runtime agents tree 的 bare Git repository。 |
| `agents/agent.yaml` | Runtime global fallback config。 |
| `agents/<core>/` | 具体 Agent Core 的 active live checkout。 |
| `.core-ignore` | Host-owned Git ignore file，用于 `__pycache__/` 等 runtime cache artifacts。 |
| `.evolve/runs/<run_id>/agents/` | 针对整个 agents tree 的隔离 evolve worktree。 |
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
```

Loader 会读取 `agent.yaml`，解析 `runtime.surface_root`，然后当 surface root 是 `agent` 时要求 `agent/pipelines.yaml`。

`packages.yaml` 是 package provenance state。它记录 installed package targets 和 hashes，但不是 runtime truth。除非你在明确指示下修复 package state，否则不要手动编辑它。

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

Skills、schedules 和 MCP roots 会从 `runtime.surface_root` 推断，除非另有配置。Bootstrap、input 和 output 始终从 `runtime.surface_root` 解析。

## Managed Checkout

Managed installs 可能把 checkout 放在：

```text
~/.demiurge/demiurge-agent/
```

Runtime cores 仍位于 `~/.demiurge/agents/` 下；managed checkout 不是 active runtime core。

## Local Agent Edits

直接修改 `~/.demiurge/agents/` 下的文件会产生 local agent edits。Demiurge 会在
run/edit workflows 加载 live core 前，把这些 edits 保存为 `~/.demiurge/.core.git`
中的 commits。生成的 commit message 是确定性的，包含 changed scopes、changed paths、
detected semantic YAML changes 和 gate status。

`demiurge core diff` 是只读命令。`demiurge core save` 会验证并提交当前 edits。
`demiurge core discard --yes` 会把 checkout 重置到 `refs/demiurge/live`，并移除未跟踪
edits。Promotion 和 rollback 不会自动保存；它们会拒绝 dirty live tree，避免 revision
switch 静默覆盖本地文件。

## Core Git Refs

Runtime core revisions 是 `~/.demiurge/.core.git` 中的 Git commits。

| Ref | Meaning |
| --- | --- |
| `refs/demiurge/live` | checkout 到 `~/.demiurge/agents/` 的 commit。 |
| `refs/demiurge/previous` | 默认 rollback target。 |
| `refs/demiurge/runs/<run_id>` | 已 review 的 evolve proposal commit。 |

`demiurge init` 会在 fresh runtime home 上从 source `agents/` tree 创建 repository。它不会迁移旧的 `registry/`、`history/` 或 `runs/` layouts。
