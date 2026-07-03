---
title: 运行时主目录
description: 理解 ~/.demiurge 下的本地 runtime 目录布局。
---

# 运行时主目录

Demiurge 是 local-first 的。Runtime 状态保存在 runtime home 下，通常是：

```text
~/.demiurge
```

源代码 checkout 和 runtime home 的职责不同。

## 主体布局

```text
~/.demiurge/
  config.yaml
  .env
  .core.git/
  .core.lock
  .evolve/
    runs/
  agents/
    agent.yaml
    assistant/
    evolver/
  runtime/
    runtime.sqlite3
    artifacts/
    session-events/
  workspace/
  logs/
```

`config.yaml` 是 host-owned 的 runtime 配置。`.env` 可以保存本地 provider
密钥。`.core.git/` 是 runtime agents tree 的 bare Git repository，`agents/`
是该 tree 的 live checkout。`.evolve/` 保存隔离 change-set worktrees。`runtime/`
包含 SQLite control-plane database、delivery outbox projection、scheduler runtime
projections、session event logs 和 host-owned artifacts。`workspace/` 是非本地的
fallback workspace。

## 源模板与 Runtime Core

仓库中的源模板位于：

```text
agents/
```

在 fresh runtime home 上，`demiurge init` 会把这棵 tree commit 到：

```text
~/.demiurge/.core.git
```

并把 live agents tree checkout 到：

```text
~/.demiurge/agents/
```

如果要修改本地行为，就编辑 runtime cores。只有在你要修改默认打包项目行为时，才编辑
源模板。此版本不迁移 legacy runtime homes；如果旧版本创建过 `~/.demiurge`，首次运行
前应删除旧 runtime home。

## Local Agent Edits

你可以直接编辑 `~/.demiurge/agents/` 下的文件。Demiurge 会把这些 changes 当作
local agent edits，并在明确的 workflow 边界保存为 Git-backed core revisions。

run/edit workflows、package install/uninstall、`setup model set` 和 `evolve start`
会先验证并保存 local agent edits，然后继续执行。这个保存会成为独立 commit，不会和
package、setup 或 evolve transaction 混在同一个 revision 里。

`core status`、`core diff`、package preview 和 package list 这类只读命令不会保存或丢弃
任何内容。`evolve promote` 和 rollback 这类 revision switch 命令如果发现仍有 local
agent edits，会拒绝切换，因为切换可能覆盖或遗留这些文件。

需要手动管理 edits 时使用：

```bash
uv run demiurge core diff
uv run demiurge core save
uv run demiurge core discard --yes
```

## 托管 Checkout

Managed install 会把 checkout 放在：

```text
~/.demiurge/demiurge-agent
```

Live runtime cores 仍然是独立 Git revisions，所以更新 managed checkout 不会覆盖已经
编辑过的 Agent Core。

## 漂移

在刷新 runtime 文件之前，先做只读 drift 检查：

```bash
uv run demiurge init --check
uv run demiurge doctor
```

有意刷新时。Refresh 是一个 Git transaction，会从 source templates 创建新的 live
revision：

```bash
uv run demiurge init --refresh assistant
```

检查 live repository：

```bash
uv run demiurge core status
uv run demiurge core versions
uv run demiurge core check
```
