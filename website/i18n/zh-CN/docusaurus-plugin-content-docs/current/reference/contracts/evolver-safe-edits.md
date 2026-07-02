---
title: Evolver-Safe Edit 合约
description: Host-managed evolver core 的稳定规则。
---

# Evolver-Safe Edit 合约

`evolver` core 会在 active core 请求 evolution 之后，编辑另一个 Agent Core 的 candidate copy。Host 创建 candidate workspace，并执行 gating 和 promotion。

这个 contract 定义 candidate work 的 safe edit scope。

## Candidate Scope

可编辑目标是 candidate concrete core，不是 global fallback config，也不是 source checkout。

安全的 candidate shape：

```text
candidate/
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
    tests/
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
agent/tests/
agent.yaml
```

只有在为了保持 candidate 可加载，或为了声明必要的 authored-surface capability、tool root、MCP root、schedule root、channel config 或 metadata override 而必须修改时，才改 `agent.yaml`。

## Forbidden Paths

不要编辑：

- source checkout files
- host config
- `agents/agent.yaml` global fallback config
- registry files
- session records
- runtime SQLite files
- scheduler/runtime task state
- production state
- release files
- dependency files
- runtime files outside the candidate workspace
- `.temp/` reference checkouts
- package repository source files unless the explicit goal is package authoring
  and the candidate workspace contains them

## Forbidden Actions

不要：

- promote a candidate manually
- roll back the active pointer manually
- install dependencies
- change the host lock file
- run broad destructive cleanup
- edit files outside the candidate workspace
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
- candidate files edited
- verification performed
- limitations or follow-up needed

Host 会执行 manifest checks 和 promotion。
