---
title: Evolver-Safe Edit Contract
description: Host-managed evolver core 的稳定规则。
---

# Evolver-Safe Edit Contract

`evolver` core 会在 active core 请求 evolution 后，编辑另一个 Agent Core 的
candidate copy。本页定义安全 edit scope。

## Allowed Candidate Paths

优先编辑：

```text
agent/skills/
agent/tools/
agent/input/
agent/output/
agent/bootstrap/
```

可以谨慎编辑：

```text
agent/SOUL.md
agent/schedules/
agent/mcp/
agent/lib/
agent/tests/
```

只有当 authored-surface edit 后必须修改 `agent.yaml` 才能保持 candidate loadable
时，才修改 `agent.yaml`。

## Forbidden Paths

不要编辑：

- source checkout files
- host config
- registry files
- session records
- scheduler state
- production state
- release files
- dependency files
- runtime files outside the candidate workspace
- `.temp/` reference checkouts
- package repository source files，除非明确目标是编写 package，且 candidate
  workspace 中包含这些文件

## Forbidden Actions

不要：

- promote a candidate manually
- roll back the active pointer manually
- install dependencies
- change the host lock file
- run broad destructive cleanup
- edit files outside the candidate workspace
- bypass host file, terminal, network, or state capabilities

## Good Evolution Goals

好的目标是 functional 且 scoped：

```text
Add an input module that gives Telegram replies a concise style hint.
Change only agent/input and agent/input/pipeline.yaml.
```

坏目标会要求 host runtime changes、dependency changes、release changes 或无边界重写。

## Finish Criteria

Evolution run 结束时，总结：

- changed behavior
- candidate files edited
- verification performed
- any limitations or follow-up needed

Host 会执行 manifest checks 和 promotion。
