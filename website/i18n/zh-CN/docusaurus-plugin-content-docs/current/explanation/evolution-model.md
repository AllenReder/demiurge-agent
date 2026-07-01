---
title: Evolution Model
description: 理解 candidate Agent Core evolution、promotion 和 rollback。
---

# Evolution Model

Demiurge 把 Agent Core 视为 versionable filesystem surface。

Evolution 不是对 host runtime 的任意自修改。它是 host-owned workflow：编辑一个 core
的 candidate copy，并且只在检查通过后 promote。

Agent Slots 是主要 evolution surface。Candidate core 可以替换、重排或组合 slot
behavior，同时 host 仍把 provider calls、tools、approvals、state、promotion 和
rollback 留在稳定 contracts 后面。

## 当前流程

1. Active core 通过 host tool runtime 请求 evolution。
2. Host 创建 active core 的 candidate copy。
3. Host 使用 candidate-scoped editing tools 运行 `evolver` core。
4. Host 检查 candidate manifest 仍然可以加载。
5. 如果 candidate 修改了文件并通过检查，host promote candidate。
6. Host 记录 version pointer。

Rollback 也由 host 拥有。

## Evolver Scope

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
- registry files
- sessions
- production state
- release files
- dependency files
- candidate 之外的 runtime files
- `.temp/` reference checkouts

它也不能 promote、roll back、安装 dependencies 或绕过 host capabilities。

## Contract

精确的 agent-readable 规则见
[/docs/reference/contracts/evolver-safe-edits](/docs/reference/contracts/evolver-safe-edits)。
