---
title: Slot Manifests and Pipelines Reference
description: Reference for Agent Slot metadata files and phase pipelines.
---

# Slot Manifests and Pipelines Reference

Bootstrap, input, and output slots are loaded from the concrete core's
`runtime.surface_root`. With the default `surface_root: agent`, the directory
contract is:

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

The slot id is the directory name. Slot metadata lives in `slot.yaml`; phase
ordering lives in `<surface_root>/pipelines.yaml`.

## `agent/pipelines.yaml`

The loader requires `pipelines.yaml` in `runtime.surface_root`:

```yaml
schema_version: 1
bootstrap:
  serial:
    - session_context
input:
  serial:
    - base_input
  parallel: []
output:
  serial:
    - base_output
  parallel: []
```

`schema_version` must be `1`. Supported phases are `bootstrap`, `input`, and
`output`.

When adding a slot, edit the existing phase list. Do not replace the whole file
unless you intend to rewrite all pipelines.

## Pipeline Rules

| Rule | Behavior |
| --- | --- |
| Unknown phase | Core load fails. |
| Unknown phase key | Core load fails. |
| Unknown slot id in a pipeline | Core load fails. |
| Duplicate slot id across phase directories | Core load fails. |
| Duplicate slot id inside one pipeline | Core load fails. |
| `bootstrap.parallel` | Core load fails. |

Bootstrap supports only `serial`. Input and output support both `serial` and
`parallel`.

## Lane Semantics

| Phase/lane | Semantics |
| --- | --- |
| `bootstrap.serial` | Runs once per session before the first turn. |
| `input.serial` | Runs before the provider call and can modify the current prompt. |
| `input.parallel` | Background input side effects; cannot modify the current prompt. |
| `output.serial` | Runs after the provider response and can write history or result data. |
| `output.parallel` | Background output side effects; cannot write session history or result data. |

`base_input` and `base_output` are editable seed slots from the default core.
They are not hidden host built-ins.

## `slot.yaml`

Accepted fields are exactly:

```yaml
entrypoint: module:process
description: "Adds current-turn context."
input_schema: {}
capabilities: []
timeout_seconds: null
failure_policy: soft
default_placement: pre_current_user
history_policy: persist
```

| Field | Default | Meaning |
| --- | --- | --- |
| `entrypoint` | `module:process` | Slot handler. Use `module:function` relative to the slot directory, or a core-root-relative Python file path plus function. |
| `description` | `""` | Description for inspection and docs. |
| `input_schema` | `{}` | Optional authored metadata. |
| `capabilities` | `[]` | Capabilities available to this slot through `ctx.capability.require(...)`. |
| `timeout_seconds` | `null` | Loaded as metadata; the current slot runtime does not enforce it. |
| `failure_policy` | `soft` | `soft` logs and continues; `hard` raises the slot failure. |
| `default_placement` | `pre_current_user` | Default placement metadata for legacy context contribution shapes. |
| `history_policy` | `persist` | Default delivery history policy. |

Unknown fields are rejected. Legacy aliases such as `run` and `failure` are not
accepted.

## Entrypoints

The common shape is:

```yaml
entrypoint: module:process
```

```python
def process(ctx):
    ...
```

Relative imports inside a slot directory are isolated per slot. Shared helper
code for the default surface can live under `agent/lib/`.

## Failure Policy

Use `failure_policy: soft` for optional behavior. A soft failure emits module
failure events and the turn continues.

Use `failure_policy: hard` only when the turn cannot proceed without the slot,
such as the seed `base_input` slot that writes the current user message.

## History Policy

Valid values are:

- `persist`
- `model_hidden`
- `transient`

`persist` writes visible output into model-visible session history.
`model_hidden` writes session history that is not included in later model
context. `transient` delivers live output without writing assistant history.
