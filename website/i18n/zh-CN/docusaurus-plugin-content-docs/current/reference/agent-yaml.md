---
title: agent.yaml 参考
description: 全局 fallback 和具体 Agent Core manifest 的参考说明。
---

# `agent.yaml` 参考

`agent.yaml` 有两种角色：

- `~/.demiurge/agents/agent.yaml` 是全局 fallback 层。
- `~/.demiurge/agents/<core>/agent.yaml` 是具体的 Agent Core manifest。

fallback 文件不是 Agent Core。它只提供默认值。

## 具体 Core 形状

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

## 顶层字段

| Field | Meaning |
| --- | --- |
| `schema_version` | Manifest schema 版本。当前默认值是 `1`。 |
| `agent` | Core 身份和版本元数据。具体 cores 必填。 |
| `runtime` | 由 host 拥有的 runtime 参数。 |
| `model` | provider 和 model 默认值。 |
| `ui` | UI 偏好，例如 tool display 级别。 |
| `channels` | 外部 channel 配置。 |
| `slots` | core 相对的 authored surface roots。它映射的是诸如 `agent/input` 这样的 root，不是单个 Agent Slot 的列表。 |
| `tools` | 内置 toolset 和 tool metadata 覆盖。 |
| `approval` | approval policy 覆盖。 |
| `capabilities` | capability 配置。 |
| `dependencies` | runtime dependency mode。 |
| `tests` | candidate test 和 smoke 元数据。 |

## Runtime 字段

| Field | Default | Meaning |
| --- | --- | --- |
| `surface_root` | `agent` | 包含 authored surface 的目录。 |
| `max_model_steps` | `90` | model/tool loop 的最大步数。 |
| `workspace` | `null` | 非本地运行时使用的 core 默认 workspace。 |

`runtime.workspace` 必须是非空字符串或者 `null`。

## Model 字段

| Field | Meaning |
| --- | --- |
| `provider` | provider profile id、`auto`、`fake` 或 `null`。 |
| `model_name` | 该 core 的 model 名称覆盖值。 |
| `model_options` | 由 host 传入的 provider-specific 选项。 |

## Toolsets

内置 toolset：

| Toolset | Includes |
| --- | --- |
| `coding` | `read_file`, `write_file`, `patch`, `search_files`, `terminal`, `job`, `process`, `web_extract`, `skills_list`, `skill_view`, `skill_manage`, `todo`, `clarify`, `session_search`. |
| `demiurge_control` | `tools_list`, `evolve_core`, `rollback_core`. |
| `schedule` | `schedule_manage`. |

## Channel Sections

支持的 channel 名称：

- `telegram`
- `webhook`
- `slack`
- `mattermost`
- `matrix`
- `email`

未知的 channel 配置会作为 disabled 或 opaque config 加载，但只有已知 channel 类型才有 runtime bridge。

## Dependency 字段

```yaml
dependencies:
  mode: host_shared
  allow_additional_dependencies: false
```

`host_shared` 是默认 runtime mode。Candidate cores 不能自动添加 Python dependencies。

## Boundary

`agent.yaml` 是 core-owned configuration。它不会赋予 slots 去拥有 provider calls、dependency installation、promotion、rollback 或 host state 的权限。
