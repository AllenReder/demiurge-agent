---
title: Write an Agent Slot
description: Add bootstrap, input, or output behavior to an Agent Core.
---

# Write an Agent Slot

Use an Agent Slot when a core needs authored behavior at a governed point in
the agent loop:

- `bootstrap` adds session-stable context once per session.
- `input` shapes the current turn before the provider call.
- `output` handles the final model output after the provider call.

Slots live under the concrete core's authored surface. With the default
`runtime.surface_root: agent`, the slot roots are:

| Phase | Root |
| --- | --- |
| `bootstrap` | `agent/bootstrap/<slot_id>/` |
| `input` | `agent/input/<slot_id>/` |
| `output` | `agent/output/<slot_id>/` |

Changing `slots.input` or `slots.output` in `agent.yaml` does not move these
phase roots. The loader resolves them from `runtime.surface_root`.

## Create the Slot Directory

For an input slot named `style_hint`, create:

```text
agent/input/style_hint/
  module.py
  slot.yaml
```

The directory name is the slot id used in `agent/pipelines.yaml`.

## Write the Module

Input slot:

```python
def process(ctx):
    ctx.input.add_context(
        "Prefer short, concrete answers for this turn.",
        role="system",
    )
```

Bootstrap slot:

```python
def process(ctx):
    ctx.bootstrap.add("Session note: this core should be concise.")
```

Output slot:

```python
def process(ctx):
    ctx.output.send_text(ctx.output.response_text)
```

The default entrypoint is `module:process`, relative to the slot directory.

## Declare `slot.yaml`

Create `slot.yaml` next to `module.py`:

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

Accepted fields are exactly:

| Field | Default | Notes |
| --- | --- | --- |
| `entrypoint` | `module:process` | `module:function`, or a core-root-relative Python file path plus function. |
| `description` | `""` | Human-facing description for inspection. |
| `input_schema` | `{}` | Author metadata; the slot loader accepts it. |
| `capabilities` | `[]` | Capabilities this slot may require through `ctx.capability.require(...)`. |
| `timeout_seconds` | `null` | Loaded as metadata; the current slot invoker does not enforce a timeout. |
| `failure_policy` | `soft` | `soft` logs and continues; `hard` fails the turn or bootstrap. |
| `default_placement` | `pre_current_user` | Default placement metadata for legacy context contribution shapes. |
| `history_policy` | `persist` | Default delivery history policy for output/tool-style sends. |

Unknown fields are rejected.

## Add the Slot to the Existing Pipeline

Open the existing `agent/pipelines.yaml`. Insert the new slot id into the
appropriate existing list.

For an input slot that should run before raw user text is appended:

```yaml
input:
  serial:
    - style_hint
    - base_input
```

For an output slot that should run after the seed output delivery:

```yaml
output:
  serial:
    - base_output
    - archive_summary
```

Do not replace the whole file. Preserve the current `schema_version`,
`bootstrap`, other phase entries, and any existing `parallel` lists unless the
change intentionally modifies them.

## Choose Serial or Parallel

Use `serial` when the slot must affect the main flow. Serial input modules can
modify the prompt; serial output modules can write history and set results.

Use `parallel` only for background side effects. Parallel input modules cannot
modify the current prompt. Parallel output modules cannot write session history
or modify the current agent result.

Bootstrap supports only `serial`.

## Verify

Run:

```bash
uv run demiurge init --check
uv run demiurge --provider fake
```

For evolution worktrees, keep edits inside the authored surface and follow
[the evolver-safe edit contract](../reference/contracts/evolver-safe-edits.md).
