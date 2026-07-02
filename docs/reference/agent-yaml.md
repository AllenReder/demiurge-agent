---
title: agent.yaml Reference
description: Reference for global fallback and concrete Agent Core manifests.
---

# `agent.yaml` Reference

`agent.yaml` appears in two roles:

- `~/.demiurge/agents/agent.yaml` is a global fallback layer.
- `~/.demiurge/agents/<core>/agent.yaml` is a concrete Agent Core manifest.

The fallback file is not an Agent Core. It provides defaults.

## Concrete Core Shape

```yaml
schema_version: 1
agent:
  id: assistant
  version: "0001"
  parent: null
  summary: "Initial Demiurge assistant core."
runtime:
  surface_root: agent
  max_model_steps: 90
  workspace: null
model:
  provider: auto
  model_name: null
  model_options: {}
ui:
  tool_display: null
channels: {}
slots:
  soul: agent/SOUL.md
  input: agent/input
  output: agent/output
  tools: agent/tools
  skills: agent/skills
  mcp: agent/mcp
  schedules: agent/schedules
tools:
  toolsets:
    - coding
    - demiurge_control
    - schedule
  metadata: {}
approval:
  default: null
  tools: {}
  capabilities: {}
  risks: {}
capabilities: {}
dependencies:
  mode: host_shared
  allow_additional_dependencies: false
tests:
  commands: []
  smoke:
    fake_llm_script: agent/tests/fixtures/fake_llm/basic_turn.json
```

## Top-Level Fields

| Field | Meaning |
| --- | --- |
| `schema_version` | Manifest schema version. Current default is `1`. |
| `agent` | Core identity and version metadata. Required for concrete cores. |
| `runtime` | Runtime parameters owned by the host. |
| `model` | Provider and model defaults. |
| `ui` | UI preferences such as tool display level. |
| `channels` | External channel configuration. |
| `slots` | Core-relative authored surface roots. This maps roots such as `agent/input`; it is not a list of individual Agent Slots. |
| `tools` | Built-in toolsets and tool metadata overrides. |
| `approval` | Approval policy overrides. |
| `capabilities` | Capability configuration. |
| `dependencies` | Runtime dependency mode. |
| `tests` | Candidate test and smoke metadata. |

## Runtime Fields

| Field | Default | Meaning |
| --- | --- | --- |
| `surface_root` | `agent` | Directory containing the authored surface. |
| `max_model_steps` | `90` | Maximum model/tool loop steps. |
| `workspace` | `null` | Core default workspace for non-local runs. |

`runtime.workspace` must be a non-empty string or `null`.

## Model Fields

| Field | Meaning |
| --- | --- |
| `provider` | Provider profile id, `auto`, `fake`, or `null`. |
| `model_name` | Model name override for this core. |
| `model_options` | Provider-specific options passed by the host. |

## Toolsets

Built-in toolsets:

| Toolset | Includes |
| --- | --- |
| `coding` | File, terminal, search, process, web extract, skills, todo, clarify, session search. |
| `demiurge_control` | `tools_list`, child-task delegation controls, `evolve_core`, `rollback_core`. |
| `schedule` | `schedule_manage`. |

## Channel Sections

Supported channel names:

- `telegram`
- `webhook`
- `slack`
- `mattermost`
- `matrix`
- `email`

Unknown channel configs are loaded as disabled or opaque configs, but only known
channel types have runtime bridges.

## Dependency Fields

```yaml
dependencies:
  mode: host_shared
  allow_additional_dependencies: false
```

`host_shared` is the default runtime mode. Candidate cores must not add Python
dependencies automatically.

## Boundary

`agent.yaml` is core-owned configuration. It does not give slots permission to
own provider calls, dependency installation, promotion, rollback, or host state.
