---
title: Slot Module Contract
description: Bootstrap、input、output 和 authored tool modules 的稳定规则。
---

# Slot Module Contract

Slot modules 是由 host 加载的 core-local extension points。它们必须留在 Agent
Core authored surface 内。

## Directory Contract

```text
agent/input/<slot_id>/
  slot.yaml
  module.py
```

同样的形状适用于：

- `agent/bootstrap/<slot_id>/`
- `agent/input/<slot_id>/`
- `agent/output/<slot_id>/`
- `agent/tools/<tool_id>/`

## Entrypoints

Bootstrap、input 和 output slots 通常使用：

```yaml
entrypoint: module:process
```

```python
def process(ctx):
    ...
```

Authored tools 通常使用：

```yaml
entrypoint: module:execute
```

```python
def execute(ctx, args):
    ...
```

## Pipelines

Input 和 output pipelines 支持：

```yaml
serial: []
parallel: []
```

Bootstrap pipeline 支持：

```yaml
serial: []
```

规则：

- 每个 pipeline entry 都必须是已知 slot id。
- 同一个 pipeline 中，一个 slot id 只能出现一次。
- Bootstrap 不支持 `parallel`。
- 未知 pipeline keys 会导致 core loading 失败。

## Capability Rule

Slots 应该在 `slot.yaml` 中声明它们需要的 capabilities，但是否允许效果发生由 host
决定。

当 host capability 已经覆盖某个效果时，不要通过直接访问 paths、network 或 process
state 来绕过 host tools。

## Failure Rule

除非缺少该 slot 会导致 turn 无法继续，否则使用 `failure_policy: soft`。对于 raw
input passthrough 这类必需基础行为，使用 `failure_policy: hard`。

## 验证

```bash
uv run demiurge init --check
uv run demiurge --provider fake
```
