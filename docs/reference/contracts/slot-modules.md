---
title: Slot Module Contract
description: Stable rules for bootstrap, input, output, and authored tool modules.
---

# Slot Module Contract

Slot modules are core-local extension points loaded by the host. They must stay
inside the Agent Core authored surface.

## Directory Contract

```text
agent/input/<slot_id>/
  slot.yaml
  module.py
```

The same shape applies to:

- `agent/bootstrap/<slot_id>/`
- `agent/input/<slot_id>/`
- `agent/output/<slot_id>/`
- `agent/tools/<tool_id>/`

## Entrypoints

Bootstrap, input, and output slots normally use:

```yaml
entrypoint: module:process
```

```python
def process(ctx):
    ...
```

Authored tools normally use:

```yaml
entrypoint: module:execute
```

```python
def execute(ctx, args):
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

## Failure Rule

Use `failure_policy: soft` unless the turn cannot proceed without the slot.
Use `failure_policy: hard` for required base behavior such as raw input
passthrough.

## Verification

```bash
uv run demiurge init --check
uv run demiurge --provider fake
```
