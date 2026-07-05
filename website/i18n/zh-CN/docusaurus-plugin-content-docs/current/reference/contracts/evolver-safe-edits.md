---
title: Evolver-Safe Edit 合约
description: Host-managed evolver core 的稳定规则。
---

# Evolver-Safe Edit 合约

`evolver` core 会在 active core 请求 evolution 之后，编辑 runtime agents tree 的隔离 Git worktree。Host 创建 worktree，并执行 gating 和 promotion。

这个 contract 定义 proposal worktree 的 safe edit scope。

当 evolution goal 涉及 bootstrap、input、output、pipelines 或 slot `ctx` APIs 时，
编辑前先读这些参考：

- [编写 Agent Slot](../../how-to/write-slot-module.md)
- [Slot Context SDK](../slot-context-sdk.md)
- [Agent Slot 合约](slot-modules.md)
- [Slots YAML](../slots-yaml.md)

## Worktree Scope

可编辑目标是隔离的 agents-tree worktree，不是 source checkout，也不是 host runtime state。Evolver 通常编辑一个 target concrete core；当目标需要跨 core 行为时，也可以编辑 helper cores。

安全的 worktree shape：

```text
agents/
  agent.yaml
  <core>/
    agent.yaml
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

## Preferred Edit Paths

优先编辑：

```text
agent/skills/
agent/tools/
agent/input/
agent/output/
agent/bootstrap/
agent/pipelines.yaml
```

谨慎允许：

```text
agent/SOUL.md
agent/schedules/
agent/mcp/
agent/lib/
agent.yaml
```

只有在为了保持 edited core 可加载，或为了声明必要的 authored-surface capability、tool root、MCP root、schedule root、channel config 或 metadata override 而必须修改时，才改 `agent.yaml`。

## Forbidden Paths

不要编辑：

- source checkout files
- host config
- `agents/agent.yaml` global fallback config
- session records
- runtime SQLite files
- scheduler/runtime task state
- production state
- release files
- dependency files
- runtime files outside the isolated worktree
- `.temp/` reference checkouts
- package repository source files unless the explicit goal is package authoring
  and the isolated worktree contains them

## Forbidden Actions

不要：

- promote a proposal manually
- roll back the live Git ref manually
- install dependencies
- change the host lock file
- run broad destructive cleanup
- edit files outside the isolated worktree
- bypass host file, terminal, network, tool, channel, or state capabilities

## Pipeline Edit Rule

添加 slot 时，编辑 `agent/pipelines.yaml` 中的现有列表。

正确：

```yaml
input:
  serial:
    - concise_hint
    - base_input
```

错误：

```text
Replace pipelines.yaml with a minimal file that omits unrelated phases.
```

保留无关 phase entries 和现有 seed slots，除非目标明确要求修改它们。

## Good Evolution Goals

好的 goals 是 functional 且 scoped：

```text
Add an input module that gives Telegram replies a concise style hint.
Change only agent/input and agent/pipelines.yaml.
```

不好的 goals 会要求 host runtime changes、dependency changes、release changes 或无边界 rewrites。

## Finish Criteria

Evolution run 结束时，总结：

- changed behavior
- worktree files edited
- verification performed
- limitations or follow-up needed

Host 会通过 `CoreRepository` 执行 gates 和 promotion。
