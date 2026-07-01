---
title: Write a Slot Module
description: Add bootstrap, input, or output behavior to an Agent Core.
---

# Write a Slot Module

Slot modules are core-local Python files loaded by the host. Use them to add
session-start context, shape current-turn input, or deliver output.

## Choose a Slot Root

| Slot kind | Root | Function |
| --- | --- | --- |
| Bootstrap | `agent/bootstrap/<id>/` | Adds session-stable context before turns. |
| Input | `agent/input/<id>/` | Adds current-turn context before provider calls. |
| Output | `agent/output/<id>/` | Delivers final assistant output, artifacts, or structured results. |

## Add `slot.yaml`

```yaml
entrypoint: module:process
description: "Describe what this slot does."
failure_policy: soft
capabilities: []
```

Use `failure_policy: hard` only when the turn should fail if the slot fails.

## Add `module.py`

Input example:

```python
def process(ctx):
    ctx.input.add("system", "Prefer short, concrete answers this turn.")
```

Output example:

```python
def process(ctx):
    ctx.output.send_text(ctx.output.content, history_policy="persist")
```

## Place the Slot in a Pipeline

Input pipeline:

```yaml
serial:
  - style_hint
  - base_input
parallel: []
```

Output pipeline:

```yaml
serial:
  - base_output
parallel:
  - artifact_writer
```

Bootstrap, input, and output pipeline files live under their slot roots:

```text
agent/bootstrap/pipeline.yaml
agent/input/pipeline.yaml
agent/output/pipeline.yaml
```

## Verify

```bash
uv run demiurge init --check
uv run demiurge --provider fake
```

For candidate evolution, keep changes limited to the authored surface and read
[../reference/contracts/evolver-safe-edits.md](../reference/contracts/evolver-safe-edits.md).

## Boundary

Slot modules do not own the provider call, tool execution, session storage, or
approval flow. They run through host-owned context and delivery interfaces.
