---
title: slot.yaml Reference
description: Reference for bootstrap, input, output, and authored tool slot metadata.
---

# `slot.yaml` Reference

Slots are discovered from directories under configured slot roots. A slot
directory is loaded only when it contains `slot.yaml`.

## Module Slot

```yaml
entrypoint: module:process
description: "Adds a short current-turn hint."
capabilities: []
timeout_seconds: 10
failure_policy: soft
default_placement: pre_current_user
history_policy: persist
```

| Field | Default | Meaning |
| --- | --- | --- |
| `entrypoint` | `null` | Python entrypoint in `module:function` form. |
| `description` | `""` | Human and model-facing description. |
| `capabilities` | `[]` | Host capabilities requested by the slot. |
| `timeout_seconds` | `null` | Optional timeout for the slot. |
| `failure_policy` | `soft` | Failure behavior. Use `hard` only for required slots. |
| `default_placement` | `pre_current_user` | Default input placement for input modules. |
| `history_policy` | `persist` | Default output history policy. |

## Authored Tool Slot

```yaml
entrypoint: module:execute
description: "Return project information."
input_schema:
  type: object
  properties:
    topic:
      type: string
  additionalProperties: false
capabilities: []
```

Authored tools use `execute(ctx, args)` and return a `ToolResult` or compatible
result.

## Pipeline Files

Input and output pipelines support `serial` and `parallel`:

```yaml
serial:
  - base_input
parallel: []
```

Bootstrap pipelines support only `serial`:

```yaml
serial:
  - session_context
```

The loader rejects unknown pipeline keys, duplicate slot ids, and unknown slot
ids.

## Discovery Rules

- Slot id is the directory name.
- Slot roots are configured in `agent.yaml` `slots`.
- Non-directory children are ignored.
- Directories without `slot.yaml` are ignored.
- Duplicate slot ids for the same kind are rejected.

## Boundary

`slot.yaml` declares metadata. It does not grant effects by itself; effects are
checked by the host capability and approval system.
