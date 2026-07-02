---
title: slots.yaml Reference
description: Reference for Agent Slot metadata and phase pipelines.
---

# `slots.yaml` Reference

`agent/slots.yaml` is the single authored slot interface for bootstrap, input,
and output slots. Slot code still lives in typed folders:

```text
agent/bootstrap/<slot_id>/module.py
agent/input/<slot_id>/module.py
agent/output/<slot_id>/module.py
```

## Shape

```yaml
version: 2
slots:
  bootstrap: {}
  input:
    base_input:
      failure: hard
      capabilities: []
  output:
    base_output:
      failure: soft
      capabilities: []
pipelines:
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

## Slot Metadata

| Field | Default | Meaning |
| --- | --- | --- |
| `run` | `agent/<phase>/<slot_id>/module.py:process` | Optional handler override. |
| `description` | `""` | Human-facing description. |
| `capabilities` | `[]` | Host capabilities requested by the slot. |
| `timeout_seconds` | `null` | Optional timeout for the slot. |
| `failure` | `soft` | Failure behavior. Use `hard` only for required slots. |
| `input_schema` | `{}` | Optional authored input schema metadata. |

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
undeclared slots.
