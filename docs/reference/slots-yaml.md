---
title: Slot Manifests and Pipelines Reference
description: Reference for Agent Slot metadata files and phase pipelines.
---

# Slot Manifests and Pipelines Reference

Bootstrap, input, and output slots are directory components. Each slot owns its
own `slot.yaml`; the phase ordering lives in `agent/pipelines.yaml`.

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

## `agent/pipelines.yaml`

```yaml
schema_version: 1
bootstrap:
  serial: []
input:
  serial: [base_input]
  parallel: []
output:
  serial: [base_output]
  parallel: []
```

`base_input` and `base_output` are ordinary editable seed slots. The host does
not require those ids and does not silently replace them.

## `slot.yaml`

```yaml
entrypoint: module:process
description: Adds current-turn context.
failure_policy: soft
capabilities: []
```

| Field | Default | Meaning |
| --- | --- | --- |
| `entrypoint` | `module:process` | Slot handler. Use `module:function` relative to the slot directory, or a core-root-relative Python file path. |
| `description` | `""` | Human-facing description. |
| `capabilities` | `[]` | Host capabilities requested by the slot. |
| `timeout_seconds` | `null` | Optional timeout for the slot. |
| `failure_policy` | `soft` | Failure behavior. Use `hard` only for required slots. |
| `input_schema` | `{}` | Optional authored input schema metadata. |
| `default_placement` | `pre_current_user` | Default input placement for context fragments. |
| `history_policy` | `persist` | Default persistence policy for slot output. |

Unknown fields are rejected. Legacy aliases such as `run` and `failure` are not
accepted.

## Phase Semantics

- `bootstrap` runs at session start and supports only `serial`.
- `input` runs before the provider call and builds current-turn model context.
- `output` runs after the provider response and handles delivery, memory writes,
  artifacts, or background side effects.

## Lane Semantics

- `serial` can affect the main flow.
- `parallel` is non-blocking and cannot modify the current prompt, assistant
  response, or session history.

The loader rejects unknown phases, unknown pipeline keys, duplicate slot ids
across phases, duplicate pipeline entries, and pipeline entries that reference
missing slot directories.
