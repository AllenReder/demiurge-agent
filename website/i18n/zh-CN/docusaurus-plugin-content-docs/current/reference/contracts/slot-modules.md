---
title: Agent Slot Contract
description: Bootstrap、input 和 output slots 的稳定规则。
---

# Agent Slot Contract

Agent Slots 是由 Host 加载的可演化交互边界。它们让 Core 定义的行为逻辑在受
治理的位置介入 agent loop。Slot code 必须留在 Agent Core authored surface 内。

## Directory Contract

```text
agent/input/<slot_id>/
  slot.yaml
  module.py
```

同样的形状适用于当前 Agent Slot kinds：

- `agent/bootstrap/<slot_id>/`
- `agent/input/<slot_id>/`
- `agent/output/<slot_id>/`

## Entrypoints

Bootstrap、input 和 output slots 通常使用：

```yaml
entrypoint: module:process
```

```python
def process(ctx):
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

Slots 可以通过 host-owned interfaces 组合 tools、skills、MCP、state 或其他 agents，
前提是所需 capabilities 允许。

## Failure Rule

除非缺少该 slot 会导致 turn 无法继续，否则使用 `failure_policy: soft`。对于 raw
input passthrough 这类必需基础行为，使用 `failure_policy: hard`。

## 验证

```bash
uv run demiurge init --check
uv run demiurge --provider fake
```
