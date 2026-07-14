---
title: agent.yaml Reference
description: Reference for the global fallback file and concrete Agent Core manifests.
---

# `agent.yaml` Reference

`agent.yaml` appears in two different places with different meanings.

| Path | Role |
| --- | --- |
| `~/.demiurge/agents/agent.yaml` | Global fallback config. Not an Agent Core. |
| `~/.demiurge/agents/<core>/agent.yaml` | Concrete Agent Core manifest. |

The global fallback file may contain only global fields such as `model`, `ui`,
and `approval`. Concrete core files contain the authored-surface bindings and
core-specific runtime configuration.

## Loader Requirements

For a concrete core, the loader requires:

- `<core>/agent.yaml`
- the directory named by `runtime.surface_root` (default: `agent`)
- `<surface_root>/pipelines.yaml`

Bootstrap, input, and output slot directories are resolved from
`runtime.surface_root`:

```text
<surface_root>/bootstrap/
<surface_root>/input/
<surface_root>/output/
```

Skills, schedules, and MCP roots are inferred from `runtime.surface_root` unless
their `slots.*` root is configured. Authored tools are discovered from the
configured `slots.tools` root.

## Global Fallback Shape

`~/.demiurge/agents/agent.yaml`:

```yaml
model:
  provider: auto
  model_name: null
  model_options: {}
ui:
  tool_display: summary
approval:
  default: null
  tools: {}
  capabilities: {}
  risks: {}
```

Use the fallback for shared model, UI, and approval defaults. Do not put
`agent`, `runtime`, `channels`, `slots`, `tools`, `capabilities`,
or `dependencies` in the fallback file.

## Concrete Core Shape

`~/.demiurge/agents/assistant/agent.yaml`:

```yaml
schema_version: 1
agent:
  id: assistant
  summary: "Initial demiurge assistant core."
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
  tools: agent/tools
  skills: agent/skills
  schedules: agent/schedules
  mcp: agent/mcp
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
capabilities:
  defaults: {}
  slots: {}
dependencies:
  mode: host_shared
  allow_additional_dependencies: false
```

## Top-Level Fields

| Field | Required | Meaning |
| --- | --- | --- |
| `schema_version` | No | Manifest schema version. Current default is `1`. |
| `agent` | Yes | Core id and summary. Git revisions live in the host-owned core repository, not in `agent.yaml`. |
| `runtime` | No | Host runtime options for this core. |
| `model` | No | Core-level model/provider defaults. |
| `ui` | No | Core-level UI preferences such as `tool_display`. |
| `channels` | No | Gateway channel configuration. |
| `slots` | No | Roots for authored surfaces that are configurable. |
| `tools` | No | Built-in toolsets and metadata overrides. |
| `approval` | No | Core-level approval policy overrides. |
| `capabilities` | No | Capability grants for host-mediated effects. |
| `dependencies` | No | Runtime dependency mode metadata. |

## `runtime`

| Field | Default | Meaning |
| --- | --- | --- |
| `surface_root` | `agent` | Directory containing `SOUL.md`, `pipelines.yaml`, and phase slot roots. |
| `max_model_steps` | `90` | Host cap for model/tool loop steps. Valid range is `1` through `90`. |
| `workspace` | `null` | Core default workspace when no CLI/env workspace is supplied. |

`runtime.workspace` must be `null` or a non-empty string. Relative paths are
resolved from the core root.

## `slots`

| Field | Default / behavior |
| --- | --- |
| `soul` | Optional extra `SOUL.md` path. The loader also reads `<surface_root>/SOUL.md`. |
| `tools` | Authored tool root. If omitted, authored tools are not discovered. |
| `skills` | Defaults to `<surface_root>/skills` when omitted. |
| `schedules` | Defaults to `<surface_root>/schedules` when omitted. |
| `mcp` | Defaults to `<surface_root>/mcp` when omitted. |

Bootstrap, input, and output roots are not configured here. They always come
from `runtime.surface_root`.

`slots.channels` is ignored by the current loader; channels are configured in
the `channels` section.

## `tools`

Built-in toolsets:

| Toolset | Includes |
| --- | --- |
| `coding` | `read_file`, `write_file`, `patch`, `search_files`, `terminal`, `web_extract`, `skills_list`, `skill_view`, `skill_manage`, `todo`, `clarify`, `session_search` |
| `demiurge_control` | `tools_list`, `task_list`, `delegate_task`, `task_status`, `task_control`, `yield_until`, `evolve_core`, `rollback_core` |
| `schedule` | `schedule_manage` |

Use `tools.metadata` to override registry metadata:

```yaml
tools:
  metadata:
    terminal:
      approval_policy: deny
    project_note:
      enabled: false
```

Supported metadata keys are `risk`, `capability`, `approval_policy`,
`model_output_policy`, `display_policy`, and `enabled`.

## `channels`

Supported channel names:

- `telegram`
- `webhook`
- `slack`
- `mattermost`
- `matrix`
- `email`

Unknown channel configs can be parsed as opaque configs, but enabled unknown
channels have no runtime bridge and fail gateway startup.

## `capabilities`

Capabilities are grants checked by the host:

```yaml
capabilities:
  defaults:
    fs.read:
      scope: workspace
    mcp.connect:docs:
      scope: core
    mcp.call:docs:
      scope: core
  slots:
    agent/output/archive_summary:
      fs.write:
        scope: workspace
```

The host treats capability keys and prefix wildcards as grants. For example,
`mcp.connect:*` can grant `mcp.connect:docs`, and `mcp.call:*` can grant
`mcp.call:docs`.

Slot and authored-tool manifests can also declare a `capabilities` list for
that specific component.

## `approval`

Approval policies are `auto`, `prompt`, or `deny`:

```yaml
approval:
  default: null
  tools:
    terminal: prompt
  capabilities:
    fs.write: prompt
  risks:
    critical: deny
```

For terminal calls, `auto` applies only after the Host command guard returns
`allow/low`. It does not override `prompt/high`, unknown shell forms, or
hardline blocks.

The host combines tool metadata, core approval config, and global fallback
approval config when deciding whether to prompt.

## `dependencies`

```yaml
dependencies:
  mode: host_shared
  allow_additional_dependencies: false
```

`host_shared` is the current default runtime mode. Candidate Agent Cores must
not add Python dependencies automatically. Record dependency needs for manual
review instead.
