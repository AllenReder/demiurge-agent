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
| `runtime/runtime.sqlite3.v4.bak` | schema 4 升级到 schema 5 前保留的、通过 integrity check 的 migration backup。 |
| `runtime/runtime.sqlite3.migrate.lock` | 串行化 runtime schema upgrade attempt 的 Host migration lock。 |
| `runtime/artifacts/` | Runtime records 引用的 host-owned artifacts。 |
| `runtime/session-events/` | Per-session diagnostic event logs。 |
| `state/<core_id>.json` | 通过 `ctx.state.core` 读写的 core-scoped JSON state。 |
| `state/sessions/<session_id>.json` | 通过 `ctx.state.session` 读写的 session-scoped JSON state。 |
| `state/proposals.jsonl` | Core 和 session state write 的 proposal audit log。 |
| `state/**/.*.transaction.json` | State snapshot 与 proposal audit 正在提交时使用的私有临时恢复 journal。 |
| `workspace/` | 未选择 CLI/env/core workspace 时使用的默认 workspace。 |
| `logs/` | Runtime logs，例如 `mcp-stderr.log`。 |

在 POSIX 上，Host 会对 runtime home 以及 `runtime/`、`logs/`、`state/` 下的 private
directory 强制 `0700`，并对 `.env`、`config.yaml`、SQLite database/WAL/SHM、event log、
state file、MCP stderr log 与 artifact 强制 `0600`。这些 mode 不依赖启动 shell 的 umask。
Startup 与会写入的 init/setup path 只收紧既有 file，不改变其 content 或 mtime。Private
write helper 拒绝 symbolic link。在 POSIX 上，directory creation、final file open、
permission tightening 与 atomic replace 都锚定到 directory descriptor，因此并发换入的
ancestor 无法把 private write 重定向到外部 tree。Windows 使用平台 ACL semantics，而不是
数字 POSIX mode。
`demiurge doctor` 只审计该 tree 并报告 `runtime.permissions.insecure`，不会自行修复权限。

Runtime schema 5 新增 immutable `session_owners` projection，供 Host-owned
`PrincipalScope` resolution 使用。新 session 会在创建时记录 conversation、operator、
system 或 delegated-agent ownership。schema 4 migration 只会安全回填唯一匹配的
conversation binding；含糊 row 会变成 `legacy_local`，只能通过显式 operator repair
path 查看；普通 origin resolution 会 fail closed，绝不会自动提升这些 row。在 POSIX 上，
migration backup 与 lock 使用 `0600`。Backup 先写入私有临时 SQLite
file，version、integrity 与 logical-fingerprint check 通过后才 atomic expose。Existing
valid-but-stale backup 会停止 migration，不会被复用。Migration 失败会 rollback database
transaction、保留 version 4 backup，并报告绝对路径与 stop-and-replace restore 动作。

这些 state file 是当前 alpha containment，不是最终 production state engine。在单个
Host process 内，同一个 resolved state path 的 write 会串行执行。Snapshot 和 proposal
audit 都通过已 flush 的同目录 temporary file 与 atomic replace 发布。在 POSIX 上，
runtime-owned state directory 固定为 `0700`，file 固定为 `0600`，不受 process umask
影响。Windows 使用平台 ACL semantics，因此不适用数字化 POSIX mode 保证。若 commit
被中断，下次读取会回滚 prepared transaction journal；若 journal 已 committed，则先
补全发布再删除 journal。

该 containment 不提供 inter-process lock。不要让多个 Demiurge Host process 共用同一个
runtime home。现有 runtime home 的 JSON document shape 不变；内部 compare-and-swap
revision 使用 content hash，不会增加 authored-state key。Proposal audit entry 还会携带
full-entropy transaction identity，因此 crash recovery 不依赖较短的 display id。最终
transactional state ownership 归 `RuntimeStore` 上的 `StateRuntime`。

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
