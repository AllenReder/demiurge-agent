---
title: 编写 Agent Slot
description: 为 Agent Core 添加 bootstrap、input 或 output 行为。
---

# 编写 Agent Slot

Agent Slot 是 Agent Core 中的可演化交互边界。使用 slot 让 Core 定义的行为逻辑在受
治理的位置进入 agent loop：添加 session-start context、塑造 current-turn input，或处理
final output。

## 选择 Slot Root

| Slot kind | Root | Function |
| --- | --- | --- |
| Bootstrap | `agent/bootstrap/<id>/` | 在 turn 前添加 session-stable context。 |
| Input | `agent/input/<id>/` | 在 provider call 前添加 current-turn context。 |
| Output | `agent/output/<id>/` | 交付 final assistant output、artifacts 或 structured results。 |

## 添加 `slot.yaml`

```yaml
entrypoint: module:process
description: "Describe what this slot does."
failure_policy: soft
capabilities: []
```

只有当 slot 失败时 turn 也应该失败，才使用 `failure_policy: hard`。

## 添加 `module.py`

Input 示例：

```python
def process(ctx):
    ctx.input.add("system", "Prefer short, concrete answers this turn.")
```

Output 示例：

```python
def process(ctx):
    ctx.output.send_text(ctx.output.content, history_policy="persist")
```

## 把 Slot 放进 Pipeline

Input pipeline：

```yaml
serial:
  - style_hint
  - base_input
parallel: []
```

Output pipeline：

```yaml
serial:
  - base_output
parallel:
  - artifact_writer
```

Bootstrap、input 和 output pipeline files 放在各自 slot roots 下：

```text
agent/bootstrap/pipeline.yaml
agent/input/pipeline.yaml
agent/output/pipeline.yaml
```

## 验证

```bash
uv run demiurge init --check
uv run demiurge --provider fake
```

Candidate evolution 时，把修改限制在 authored surface 内，并阅读
[/docs/reference/contracts/evolver-safe-edits](/docs/reference/contracts/evolver-safe-edits)。

## 边界

Agent Slots 不拥有 provider call、tool execution、session storage 或 approval flow。
它们通过 host-owned context 和 delivery interfaces 运行。
