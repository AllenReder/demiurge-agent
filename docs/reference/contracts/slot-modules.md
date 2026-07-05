---
title: Agent Slot Contract
description: Stable rules for bootstrap, input, and output slots.
---

# Agent Slot Contract

Agent Slots are governed extension points loaded from an Agent Core's authored
surface. They let core-authored code run at specific points in the host-owned
agent loop.

## Directory Contract

With `runtime.surface_root: agent`, slot directories are:

```text
agent/bootstrap/<slot_id>/
  module.py
  slot.yaml
agent/input/<slot_id>/
  module.py
  slot.yaml
agent/output/<slot_id>/
  module.py
  slot.yaml
```

The loader discovers bootstrap, input, and output slots from
`runtime.surface_root`, not from `slots.input` or `slots.output`.

## Manifest Contract

`slot.yaml` accepts exactly these fields:

```yaml
entrypoint: module:process
description: "Short description."
input_schema: {}
capabilities: []
timeout_seconds: null
failure_policy: soft
default_placement: pre_current_user
history_policy: persist
```

Unknown fields fail core loading.

## Entrypoint Contract

The normal entrypoint is:

```yaml
entrypoint: module:process
```

```python
def process(ctx):
    ...
```

Entrypoints are loaded from the slot directory unless the manifest uses a
core-root-relative Python file path.

Relative imports are isolated by slot path. Shared helper code can live under
`agent/lib/` for the default authored surface.

## Pipeline Contract

`agent/pipelines.yaml` is required:

```yaml
schema_version: 1
bootstrap:
  serial: []
input:
  serial: []
  parallel: []
output:
  serial: []
  parallel: []
```

Rules:

- `schema_version` must be `1`.
- Every pipeline entry must be a known slot id for that phase.
- A slot id can appear only once in the same pipeline.
- Bootstrap supports only `serial`.
- Unknown phases and pipeline keys fail core loading.

When adding a slot, edit the existing list and preserve unrelated phases.

## Bootstrap Context

Bootstrap runs once per session before turns begin:

```python
def process(ctx):
    ctx.bootstrap.add("Session-level context.")
```

Bootstrap return values are ignored. Use `ctx.bootstrap.add(...)` to add
session-stable context.

## Input Context

Input slots run before the provider call:

```python
def process(ctx):
    ctx.input.add_context("Prefer concise answers.", role="system")
    ctx.input.add_context(ctx.input.raw_text, role="user")
```

The seed `base_input` slot appends the raw user text. If no input slot produces
user text, the turn fails.

Serial input slots can modify the prompt. Parallel input slots cannot modify the
current prompt.

## Output Context

Output slots run after the provider response:

```python
def process(ctx):
    ctx.output.send_text(ctx.output.response_text)
```

The seed `base_output` slot delivers the model response. If no output slot
delivers or records the response, the raw provider response remains only in
runtime records.

Serial output slots can write history and result data. Parallel output slots
cannot write session history or modify the current result.

## Child Agent Calls

Input and output slot code can call child agents through `ctx.agents`.
`ctx.agents.run(...)` waits for the child turn; `ctx.agents.spawn(...)` starts
an `agent.spawn` background task.

Both calls accept `input_slots`, `output_slots`, `tools`, and `use_bootstrap`.
Omitting a slot list, passing `None`, or passing `[]` runs only `base_input` or
`base_output` in the child core. Passing `"all"` runs the child core's full
configured pipeline. Passing a non-empty list filters the child core's active
pipeline by slot id while preserving pipeline order and serial/parallel groups.

`tools` defaults to `"all"`, which keeps the child core's configured tools.
Passing `"none"` or `[]` runs the child without tools. Passing a non-empty list
allows only those configured child tool ids.

`use_bootstrap` defaults to `False`. When false, the child turn does not run
bootstrap slots and does not inject a bootstrap snapshot.

## Capability Rule

Declare capabilities in `slot.yaml` when slot code requires host-mediated
effects:

```yaml
capabilities:
  - fs.read
  - tool.call:project_note
```

Then require them in code:

```python
def process(ctx):
    ctx.capability.require("fs.read", slot_path=ctx.slot_path)
```

Do not bypass host tools, workspace scope, channel policy, or state APIs when a
host capability exists for the effect.

## Failure Rule

Use `failure_policy: soft` for optional behavior. Use `failure_policy: hard`
only when the phase cannot continue without the slot.

## Verification

After slot edits, run:

```bash
uv run demiurge init --check
uv run demiurge --provider fake
```
