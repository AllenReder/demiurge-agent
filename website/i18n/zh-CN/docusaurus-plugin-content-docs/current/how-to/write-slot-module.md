---
title: 编写 Agent Slot
description: 向 Agent Core 添加 bootstrap、input 或 output 行为。
---

# 编写 Agent Slot

当 core 需要在 agent loop 中某个受治理的位置加入 authored behavior 时，使用 Agent Slot：

- `bootstrap` 在每个 session 中添加一次 session-stable context。
- `input` 在 provider call 之前塑造当前 turn。
- `output` 在 provider call 之后处理最终 model output。

Slots 位于具体 core 的 authored surface 下。默认 `runtime.surface_root: agent` 时，slot roots 是：

| Phase | Root |
| --- | --- |
| `bootstrap` | `agent/bootstrap/<slot_id>/` |
| `input` | `agent/input/<slot_id>/` |
| `output` | `agent/output/<slot_id>/` |

修改 `agent.yaml` 中的 `slots.input` 或 `slots.output` 不会移动这些 phase roots。Loader 会从 `runtime.surface_root` 解析它们。

## 创建 Slot 目录

对于名为 `style_hint` 的 input slot，创建：

```text
agent/input/style_hint/
  module.py
  slot.yaml
```

目录名就是 `agent/pipelines.yaml` 中使用的 slot id。

## 编写 Module

Input slot：

```python
def process(ctx):
    ctx.input.add_context(
        "Prefer short, concrete answers for this turn.",
        role="system",
    )
```

Bootstrap slot：

```python
def process(ctx):
    ctx.bootstrap.add("Session note: this core should be concise.")
```

Output slot：

```python
def process(ctx):
    ctx.output.send_text(ctx.output.response_text)
```

默认 entrypoint 是相对于 slot 目录的 `module:process`。

## 声明 `slot.yaml`

在 `module.py` 旁边创建 `slot.yaml`：

```yaml
entrypoint: module:process
description: "Adds a current-turn style hint."
input_schema: {}
capabilities: []
timeout_seconds: null
failure_policy: soft
default_placement: pre_current_user
history_policy: persist
```

可接受字段严格如下：

| Field | Default | Notes |
| --- | --- | --- |
| `entrypoint` | `module:process` | `module:function`，或 core-root-relative Python file path 加 function。 |
| `description` | `""` | 用于检查的人类可读描述。 |
| `input_schema` | `{}` | Author metadata；slot loader 会接受它。 |
| `capabilities` | `[]` | 这个 slot 可能通过 `ctx.capability.require(...)` 需要的 capabilities。 |
| `timeout_seconds` | `null` | 作为 metadata 加载；当前 slot invoker 不强制 timeout。 |
| `failure_policy` | `soft` | `soft` 会记录日志并继续；`hard` 会让 turn 或 bootstrap 失败。 |
| `default_placement` | `pre_current_user` | 面向 legacy context contribution shapes 的默认 placement metadata。 |
| `history_policy` | `persist` | output/tool-style sends 的默认 delivery history policy。 |

未知字段会被拒绝。

## 将 Slot 添加到现有 Pipeline

打开现有的 `agent/pipelines.yaml`。把新的 slot id 插入对应的现有列表。

对于应该在追加原始用户文本之前运行的 input slot：

```yaml
input:
  serial:
    - style_hint
    - base_input
```

对于应该在 seed output delivery 之后运行的 output slot：

```yaml
output:
  serial:
    - base_output
    - archive_summary
```

不要替换整个文件。除非这次更改有意修改，否则保留当前的 `schema_version`、`bootstrap`、其他 phase entries 和任何现有 `parallel` 列表。

## 选择 Serial 或 Parallel

当 slot 必须影响主流程时，使用 `serial`。Serial input modules 可以修改 prompt；serial output modules 可以写入 history 并设置 results。

只把 `parallel` 用于后台副作用。Parallel input modules 不能修改当前 prompt。Parallel output modules 不能写入 session history 或修改当前 agent result。

Bootstrap 只支持 `serial`。

## 验证

运行：

```bash
uv run demiurge init --check
uv run demiurge --provider fake
```

对于 candidate evolution，请把编辑限制在 authored surface 内，并遵循 [evolver-safe edit contract](../reference/contracts/evolver-safe-edits.md)。
