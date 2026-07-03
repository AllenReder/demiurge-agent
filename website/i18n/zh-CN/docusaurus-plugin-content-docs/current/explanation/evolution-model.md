---
title: Evolution 模型
description: 理解 Git-backed Agent Core evolution、promotion 和 rollback。
---

# Evolution 模型

Demiurge 把 runtime agents tree 视为 Git-versioned filesystem surface。

Evolution 不是对 host runtime 的任意自修改。它是 host-owned workflow：编辑隔离
worktree，并且只在 review gates 通过后 promote。

Agent Slots 是主要 evolution surface。Candidate core 可以替换、重排或组合 slot
behavior，同时 host 仍把 provider calls、tools、approvals、state、Git revision
promotion 和 rollback 留在稳定 contracts 后面。

## 当前流程

1. Active core 通过 host tool runtime 请求 evolution。
2. Host 从 `refs/demiurge/live` 创建 `.evolve/runs/<run_id>/agents` Git worktree。
3. Host 使用 worktree-scoped editing tools 运行 `evolver` core。
4. Review 运行 host-owned gates，并把 proposal commit 记录到 `refs/demiurge/runs/<run_id>`。
5. Promote 重新运行 gates，推进 `refs/demiurge/previous` 和 `refs/demiurge/live`，并刷新 live checkout。

Rollback 也由 host 拥有。

## Evolver 范围

`evolver` core 可以编辑 candidate workspace 内的 authored files，尤其是：

- `agent/skills/`
- `agent/tools/`
- `agent/input/`
- `agent/output/`
- `agent/bootstrap/`

只有在保持 candidate 可加载所必需时，它才应该修改 `agent.yaml`。

## Evolution 不能做什么

Candidate evolution 不能编辑：

- source checkout files
- host config
- sessions
- production state
- release files
- dependency files
- candidate 之外的 runtime files
- `.core.git` refs directly
- `.temp/` reference checkouts

它也不能 promote、roll back、安装 dependencies 或绕过 host capabilities。

## Contract

精确的 agent-readable 规则见
[/docs/reference/contracts/evolver-safe-edits](/docs/reference/contracts/evolver-safe-edits)。
