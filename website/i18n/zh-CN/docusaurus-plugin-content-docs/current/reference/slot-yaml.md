---
title: slot.yaml 参考
description: Agent Slot 和 authored tool metadata 的参考说明。
---

# `slot.yaml` 参考

Agent Slots 是从已配置 slot roots 下的目录中发现的。只有包含 `slot.yaml` 的 slot 目录才会被加载。

## Agent Slot Metadata

```yaml
entrypoint: module:process
description: "Adds a short current-turn hint."
capabilities: []
timeout_seconds: 10
failure_policy: soft
default_placement: pre_current_user
history_policy: persist
```

| Field | Default | Meaning |
| --- | --- | --- |
| `entrypoint` | `null` | `module:function` 形式的 Python entrypoint。 |
| `description` | `""` | 面向人和 model 的描述。 |
| `capabilities` | `[]` | slot 请求的 host capabilities。 |
| `timeout_seconds` | `null` | slot 的可选超时时间。 |
| `failure_policy` | `soft` | 失败行为。只有必需 slots 才使用 `hard`。 |
| `default_placement` | `pre_current_user` | input slots 的默认输入位置。 |
| `history_policy` | `persist` | output slots 的默认输出 history policy。 |

## Authored Tool Metadata

```yaml
entrypoint: module:execute
description: "Return project information."
input_schema:
  type: object
  properties:
    topic:
      type: string
  additionalProperties: false
capabilities: []
```

Authored tools 使用 `execute(ctx, args)`，并返回 `ToolResult` 或兼容结果。它们使用同样的 `slot.yaml` 文件格式存放 metadata，但 tool 是 model 可调用的 action，而不是 Agent Slot。

## Pipeline Files

Input 和 output pipelines 支持 `serial` 和 `parallel`：

```yaml
serial:
  - base_input
parallel: []
```

Bootstrap pipelines 只支持 `serial`：

```yaml
serial:
  - session_context
```

loader 会拒绝未知 pipeline keys、重复的 slot ids，以及未知的 slot ids。

## Discovery Rules

- Slot id 就是目录名。
- Slot roots 在 `agent.yaml` 的 `slots` 中配置。
- 非目录子项会被忽略。
- 不含 `slot.yaml` 的目录会被忽略。
- 同一 kind 下重复的 slot ids 会被拒绝。

## Boundary

`slot.yaml` 只声明 metadata。它本身不会授予 effects；effects 由 host capability 和 approval system 检查。
