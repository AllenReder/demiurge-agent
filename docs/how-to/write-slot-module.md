---
title: Write an Agent Slot
description: Add bootstrap, input, or output behavior to an Agent Core.
---

# Write an Agent Slot

An Agent Slot is an evolvable interaction boundary in an Agent Core. Use a slot
to let Core-defined behavior enter the agent loop at a governed point: add
session-start context, shape current-turn input, or handle final output.

## Choose a Slot Root

| Slot kind | Root | Function |
| --- | --- | --- |
| Bootstrap | `agent/bootstrap/<id>/` | Adds session-stable context before turns. |
| Input | `agent/input/<id>/` | Adds current-turn context before provider calls. |
| Output | `agent/output/<id>/` | Delivers final assistant output, artifacts, or structured results. |

## Add the Slot Code

Create `module.py` in the slot directory. Input example:

```python
def process(ctx):
    ctx.input.add_context("Prefer short, concrete answers this turn.", role="system")
```

Output example:

```python
def process(ctx):
    ctx.output.send_text(ctx.output.response_text)
```

## Declare the Slot in `agent/slots.yaml`

Add metadata and pipeline placement in the core's single slot graph:

```yaml
version: 2
slots:
  input:
    style_hint:
      failure: soft
      capabilities: []
      description: "Adds a current-turn style hint."
pipelines:
  input:
    serial:
      - style_hint
      - base_input
    parallel: []
```

Use `failure: hard` only when the turn should fail if the slot fails. Handler
entrypoints default to `agent/<phase>/<slot_id>/module.py:process`; use `run:`
only when the function lives elsewhere.

Output pipeline entries use the same file:

```yaml
slots:
  output:
    artifact_writer:
      failure: soft
      capabilities: [fs.write]
pipelines:
  output:
    serial:
      - base_output
    parallel:
      - artifact_writer
```

## Verify

```bash
uv run demiurge init --check
uv run demiurge --provider fake
```

For candidate evolution, keep changes limited to the authored surface and read
[/docs/reference/contracts/evolver-safe-edits](/docs/reference/contracts/evolver-safe-edits).

## Boundary

Agent Slots do not own the provider call, tool execution, session storage, or
approval flow. They run through host-owned context and delivery interfaces.
