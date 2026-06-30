# `slot.yaml` Reference

Slots describe input modules, output modules, bootstrap modules, and authored
tools. The slot directory name is the slot id.

## Module Slot

```yaml
entrypoint: module:process
description: "Short human-readable description."
failure_policy: soft
history_policy: transient
capabilities: []
timeout_seconds: null
```

Fields:

| Field | Purpose |
| --- | --- |
| `entrypoint` | Python callable in `module:function` form. |
| `description` | Human-readable summary. |
| `failure_policy` | `soft` continues after failure; `hard` blocks the phase/turn. |
| `history_policy` | Default for delivery calls from this slot. |
| `capabilities` | Host capabilities granted to this slot. |
| `timeout_seconds` | Optional execution timeout. |
| `default_placement` | Default context placement for input contributions. |

## Authored Tool Slot

```yaml
entrypoint: module:run
description: "Return project information."
input_schema:
  type: object
  properties:
    topic:
      type: string
  required:
    - topic
capabilities: []
risk: medium
approval_policy: prompt
model_output_policy: content
display_policy: summary
enabled: true
```

Tool-specific fields:

| Field | Purpose |
| --- | --- |
| `input_schema` | Model-facing JSON schema. |
| `risk` | `low`, `medium`, `high`, or `critical`. |
| `approval_policy` | `auto`, `prompt`, or `deny`. |
| `capability` | Optional explicit capability name. |
| `model_output_policy` | How tool output enters model context. |
| `display_policy` | UI display preference. |
| `enabled` | Whether the tool is exposed. |

## Success Check

```bash
uv run demiurge init --check
uv run demiurge --provider fake
```

Use `/tools` for authored tools and `/events` for module failures.
