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

## Declare the Slot Manifest

Create `slot.yaml` next to `module.py`:

```yaml
entrypoint: module:process
failure_policy: soft
capabilities: []
description: "Adds a current-turn style hint."
```

Use `failure_policy: hard` only when the turn should fail if the slot fails.
Handler entrypoints default to `module:process`; use `entrypoint` only when the
function lives elsewhere.

## Add the Slot to `agent/pipelines.yaml`

Input slots run in the order declared by the input pipeline:

```yaml
schema_version: 1
bootstrap:
  serial: []
input:
  serial:
    - style_hint
    - base_input
  parallel: []
output:
  serial:
    - base_output
  parallel: []
```

For output slots, add the output slot id under `output.serial` or
`output.parallel`:

```yaml
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
