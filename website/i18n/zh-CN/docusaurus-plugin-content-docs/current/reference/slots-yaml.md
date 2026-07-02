---
title: Slot Manifests 和 Pipelines 参考
description: Agent Slot metadata 文件和 phase pipelines 的参考。
---

# Slot Manifests 和 Pipelines 参考

Bootstrap、input 和 output slots 会从具体 core 的 `runtime.surface_root` 加载。默认 `surface_root: agent` 时，目录 contract 是：

```text
agent/bootstrap/<slot_id>/
  module.py
  slot.yaml
agent/input/<slot_id>/
  module.py
  slot.yaml
agent/output/<slot_id>/
  module.py
  slot.yaml
```

Slot id 是目录名。Slot metadata 位于 `slot.yaml`；phase ordering 位于 `<surface_root>/pipelines.yaml`。

## `agent/pipelines.yaml`

Loader 要求 `runtime.surface_root` 中存在 `pipelines.yaml`：

```yaml
schema_version: 1
bootstrap:
  serial:
    - session_context
input:
  serial:
    - base_input
  parallel: []
output:
  serial:
    - base_output
  parallel: []
```

`schema_version` 必须是 `1`。支持的 phases 是 `bootstrap`、`input` 和 `output`。

添加 slot 时，编辑现有 phase list。除非你有意重写所有 pipelines，否则不要替换整个文件。

## Pipeline Rules

| Rule | Behavior |
| --- | --- |
| Unknown phase | Core load fails. |
| Unknown phase key | Core load fails. |
| Unknown slot id in a pipeline | Core load fails. |
| Duplicate slot id across phase directories | Core load fails. |
| Duplicate slot id inside one pipeline | Core load fails. |
| `bootstrap.parallel` | Core load fails. |

Bootstrap 只支持 `serial`。Input 和 output 同时支持 `serial` 和 `parallel`。

## Lane Semantics

| Phase/lane | Semantics |
| --- | --- |
| `bootstrap.serial` | 每个 session 在第一个 turn 之前运行一次。 |
| `input.serial` | 在 provider call 之前运行，可以修改当前 prompt。 |
| `input.parallel` | 后台 input side effects；不能修改当前 prompt。 |
| `output.serial` | 在 provider response 之后运行，可以写入 history 或 result data。 |
| `output.parallel` | 后台 output side effects；不能写入 session history 或 result data。 |

`base_input` 和 `base_output` 是默认 core 中可编辑的 seed slots。它们不是隐藏的 host built-ins。

## `slot.yaml`

可接受字段严格如下：

```yaml
entrypoint: module:process
description: "Adds current-turn context."
input_schema: {}
capabilities: []
timeout_seconds: null
failure_policy: soft
default_placement: pre_current_user
history_policy: persist
```

| 字段 | 默认值 | 含义 |
| --- | --- | --- |
| `entrypoint` | `module:process` | Slot handler。使用相对于 slot 目录的 `module:function`，或 core-root-relative Python file path 加 function。 |
| `description` | `""` | 用于检查和 docs 的描述。 |
| `input_schema` | `{}` | 可选 authored metadata。 |
| `capabilities` | `[]` | 这个 slot 可通过 `ctx.capability.require(...)` 使用的 capabilities。 |
| `timeout_seconds` | `null` | 作为 metadata 加载；当前 slot runtime 不强制执行。 |
| `failure_policy` | `soft` | `soft` 会记录日志并继续；`hard` 会抛出 slot failure。 |
| `default_placement` | `pre_current_user` | 面向 legacy context contribution shapes 的默认 placement metadata。 |
| `history_policy` | `persist` | 默认 delivery history policy。 |

未知字段会被拒绝。Legacy aliases（例如 `run` 和 `failure`）不被接受。

## Entrypoints

常见形状是：

```yaml
entrypoint: module:process
```

```python
def process(ctx):
    ...
```

Slot 目录内的 relative imports 按 slot 隔离。默认 surface 的共享 helper code 可以放在 `agent/lib/` 下。

## Failure Policy

可选行为使用 `failure_policy: soft`。Soft failure 会发出 module failure events，并且 turn 会继续。

只有当缺少该 slot 就无法继续 turn 时，才使用 `failure_policy: hard`，例如写入当前用户消息的 seed `base_input` slot。

## History Policy

有效值是：

- `persist`
- `model_hidden`
- `transient`

`persist` 会把可见 output 写入 model-visible session history。`model_hidden` 写入 session history，但不包含在后续 model context 中。`transient` 发送 live output，但不写入 assistant history。
