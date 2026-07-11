---
title: agent.yaml 参考
description: 全局 fallback 文件和具体 Agent Core manifest 的参考。
---

# `agent.yaml` 参考

`agent.yaml` 会出现在两个不同位置，含义不同。

| 路径 | 角色 |
| --- | --- |
| `~/.demiurge/agents/agent.yaml` | 全局 fallback config。不是 Agent Core。 |
| `~/.demiurge/agents/<core>/agent.yaml` | 具体 Agent Core manifest。 |

全局 fallback 文件只能包含 `model`、`ui` 和 `approval` 等全局字段。具体 core 文件包含 authored-surface bindings 和 core-specific runtime configuration。

## Loader 要求

对于具体 core，loader 要求：

- `<core>/agent.yaml`
- `runtime.surface_root` 命名的目录（默认：`agent`）
- `<surface_root>/pipelines.yaml`

Bootstrap、input 和 output slot 目录会从 `runtime.surface_root` 解析：

```text
<surface_root>/bootstrap/
<surface_root>/input/
<surface_root>/output/
```

Skills、schedules 和 MCP roots 会从 `runtime.surface_root` 推断，除非配置了对应的 `slots.*` root。Authored tools 会从配置好的 `slots.tools` root 发现。

## 全局 Fallback 形状

`~/.demiurge/agents/agent.yaml`：

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

使用 fallback 提供共享的 model、UI 和 approval defaults。不要把 `agent`、`runtime`、`channels`、`slots`、`tools`、`capabilities` 或 `dependencies` 放进 fallback 文件。

## 具体 Core 形状

`~/.demiurge/agents/assistant/agent.yaml`：

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

## 顶层字段

| 字段 | 必需 | 含义 |
| --- | --- | --- |
| `schema_version` | 否 | Manifest schema version。当前默认值是 `1`。 |
| `agent` | 是 | Core id 和 summary。Git revision 由 host-owned core repository 管理，不写入 manifest。 |
| `runtime` | 否 | 该 core 的 host runtime options。 |
| `model` | 否 | Core-level model/provider defaults。 |
| `ui` | 否 | Core-level UI preferences，例如 `tool_display`。 |
| `channels` | 否 | Gateway channel configuration。 |
| `slots` | 否 | 可配置 authored surfaces 的 roots。 |
| `tools` | 否 | Built-in toolsets 和 metadata overrides。 |
| `approval` | 否 | Core-level approval policy overrides。 |
| `capabilities` | 否 | Host-mediated effects 的 capability grants。 |
| `dependencies` | 否 | Runtime dependency mode metadata。 |

## `runtime`

| 字段 | 默认值 | 含义 |
| --- | --- | --- |
| `surface_root` | `agent` | 包含 `SOUL.md`、`pipelines.yaml` 和 phase slot roots 的目录。 |
| `max_model_steps` | `90` | Model/tool loop steps 的 host 上限。有效范围是 `1` 到 `90`。 |
| `workspace` | `null` | 没有提供 CLI/env workspace 时使用的 core 默认 workspace。 |

`runtime.workspace` 必须是 `null` 或非空字符串。相对路径会从 core root 解析。

## `slots`

| 字段 | 默认值 / 行为 |
| --- | --- |
| `soul` | 可选的额外 `SOUL.md` 路径。Loader 也会读取 `<surface_root>/SOUL.md`。 |
| `tools` | Authored tool root。如果省略，则不会发现 authored tools。 |
| `skills` | 省略时默认为 `<surface_root>/skills`。 |
| `schedules` | 省略时默认为 `<surface_root>/schedules`。 |
| `mcp` | 省略时默认为 `<surface_root>/mcp`。 |

Bootstrap、input 和 output roots 不在这里配置。它们始终来自 `runtime.surface_root`。

当前 loader 会忽略 `slots.channels`；channels 在 `channels` section 中配置。

## `tools`

Built-in toolsets：

| Toolset | Includes |
| --- | --- |
| `coding` | `read_file`, `write_file`, `patch`, `search_files`, `terminal`, `web_extract`, `skills_list`, `skill_view`, `skill_manage`, `todo`, `clarify`, `session_search` |
| `demiurge_control` | `tools_list`, `task_list`, `delegate_task`, `task_status`, `task_control`, `yield_until`, `evolve_core`, `rollback_core` |
| `schedule` | `schedule_manage` |

使用 `tools.metadata` 覆盖 registry metadata：

```yaml
tools:
  metadata:
    terminal:
      approval_policy: deny
    project_note:
      enabled: false
```

支持的 metadata keys 是 `risk`、`capability`、`approval_policy`、`model_output_policy`、`display_policy` 和 `enabled`。

## `channels`

支持的 channel names：

- `telegram`
- `webhook`
- `slack`
- `mattermost`
- `matrix`
- `email`

未知 channel configs 可以作为 opaque configs 解析，但启用未知 channels 没有 runtime bridge，会导致 gateway startup 失败。

## `capabilities`

Capabilities 是由 host 检查的 grants：

```yaml
capabilities:
  defaults:
    fs.read:
      scope: workspace
    mcp.call:docs:
      scope: core
  slots:
    agent/output/archive_summary:
      fs.write:
        scope: workspace
```

Host 会把 capability keys 和 prefix wildcards 视为 grants。例如，`mcp.call:*` 可以授予 `mcp.call:docs`。

Slot 和 authored-tool manifests 也可以为对应组件声明 `capabilities` 列表。

## `approval`

Approval policies 是 `auto`、`prompt` 或 `deny`：

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

Host 会结合 tool metadata、core approval config 和 global fallback approval config 来决定是否提示。
对于 terminal call，只有 Host command guard 返回 `allow/low` 时 `auto` 才生效；它不会
覆盖 `prompt/high`、unknown shell form 或 hardline block。

## `dependencies`

```yaml
dependencies:
  mode: host_shared
  allow_additional_dependencies: false
```

`host_shared` 是当前默认 runtime mode。Candidate Agent Cores 不得自动添加 Python dependencies。请改为记录 dependency needs，供人工 review。
