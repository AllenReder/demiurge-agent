---
title: Agent Slot Contract
description: Stable rules for bootstrap, input, and output slots.
---

# Agent Slot Contract

Agent Slots are evolvable interaction boundaries loaded by the host. They let
Core-defined behavior enter the agent loop at governed points. Slot code must
stay inside the Agent Core authored surface.

## Directory Contract

```text
agent/input/<slot_id>/
  slot.yaml
  module.py
```

The same shape applies to current Agent Slot kinds:

- `agent/bootstrap/<slot_id>/`
- `agent/input/<slot_id>/`
- `agent/output/<slot_id>/`

## Entrypoints

Bootstrap, input, and output slots normally use:

```yaml
entrypoint: module:process
```

```python
def process(ctx):
    ...
```

## Pipelines

Input and output pipelines support:

```yaml
serial: []
parallel: []
```

Bootstrap pipeline supports:

```yaml
serial: []
```

Rules:

- Every pipeline entry must be a known slot id.
- A slot id can appear only once in the same pipeline.
- Bootstrap does not support `parallel`.
- Unknown pipeline keys fail core loading.

## Capability Rule

Slots should declare capabilities they need in `slot.yaml`, but the host decides
whether the effect is allowed.

Do not bypass host tools by directly touching paths, network, or process state
when a host capability exists for the effect.

Slots may compose tools, skills, MCP, state, or other agents through host-owned
interfaces when the required capabilities allow it.

## Failure Rule

Use `failure_policy: soft` unless the turn cannot proceed without the slot.
Use `failure_policy: hard` for required base behavior such as raw input
passthrough.

## Verification

```bash
uv run demiurge init --check
uv run demiurge --provider fake
```
