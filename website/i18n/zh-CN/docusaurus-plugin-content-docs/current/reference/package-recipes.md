---
title: Package Recipe 参考
description: Package recipe YAML 字段、options、components、config overlays、conditions 和 validation rules 的完整参考。
---

# Package Recipe 参考

Package recipes 位于 package repository 的：

```text
packages/<package_id>.yaml
```

Recipe 描述要把哪些 authored-surface files 安装进 runtime Agent Cores。它拥有 package identity、install-time options、component selection、target paths、pipeline placement、conditions 和 config overlays。

Component source directories 拥有 runtime files，例如 `slot.yaml`、`tool.yaml`、`config.yaml`、Python modules、skills、libraries、child cores、MCP manifests 和 schedule manifests。

任务导向的写作指南见 [编写 Package Recipe](../how-to/write-package-recipe.md)。

## 完整形状

```yaml
schema_version: 1
id: reply_style
name: Reply Style
summary: Add a reply style input module.
tags:
  - input
  - style
manual_dependencies: []
options:
  - id: tone
    type: choice
    prompt: Tone
    description: Choose the reply tone.
    default: direct
    required: false
    choices:
      - value: direct
        description: Prefer direct answers.
      - value: detailed
        description: Prefer detailed answers.
capabilities: []
components:
  - id: reply_style_input
    kind: input
    source: reply_style
    target: agent/input/reply_style
    pipeline:
      group: serial
      append: true
    config:
      tone: ${options.tone}
```

顶层只有 `schema_version`、`id` 和 `components` 是必需字段。

## 顶层字段

| 字段 | 是否必需 | 类型 | 默认值 | 含义 |
| --- | --- | --- | --- | --- |
| `schema_version` | 是 | integer | None | 必须是 `1`。 |
| `id` | 是 | string | None | Package id，在 repository 内唯一。 |
| `name` | 否 | string | `id` | Package list 和交互式流程中的显示名称。 |
| `summary` | 否 | string | empty string | 简短 package summary。 |
| `tags` | 否 | list of strings | `[]` | 用于 filter 和 browse 的 tags，例如 `memory`、`stt` 或 `provider:openai`。 |
| `manual_dependencies` | 否 | list of strings | `[]` | Demiurge 不会安装的 dependencies 的人工 review warnings。 |
| `options` | 否 | list of option objects | `[]` | 用户可提供的 install-time values。 |
| `capabilities` | 否 | list of strings | `[]` | 用于 review 的 package-level capability summary。 |
| `components` | 是 | list of component objects | None | 要安装的 components。 |

已移除的 v1 字段会被拒绝：

```text
slots
tools
files
config_defaults
metadata
```

Component-level `metadata` 和 `phase` 也会被拒绝。

## Option 字段

Options 只在 install 或 preview 时解析一次。脚本传入未知 options 会被拒绝。

| 字段 | 是否必需 | 类型 | 默认值 | 含义 |
| --- | --- | --- | --- | --- |
| `id` | 是 | string | None | `${options.<id>}` references 使用的 option id。在 recipe 内必须唯一。 |
| `type` | 否 | string | `string` | `string`、`bool`、`choice`、`path` 或 `secret`。 |
| `prompt` | 否 | string | `id` | 交互式 package manager 显示的 label。 |
| `description` | 否 | string | empty string | 交互式流程和文档中显示的 help text。 |
| `default` | 否 | any scalar | absent 时为 `null` | 用户没有提供答案时使用的值。 |
| `required` | 否 | boolean | `false` | Defaults applied 后仍 missing 或 empty 时拒绝安装。 |
| `choices` | 对 `choice` 必需 | list | `[]` | `choice` option 允许的 values。 |
| `secret` | 否 | boolean | `type: secret` 时为 `true` | 在已安装的 `packages.yaml` 中脱敏该 option。 |

支持的 option types：

| 类型 | 接受的 install value | 说明 |
| --- | --- | --- |
| `string` | scalar value | 存为 text。Lists 和 maps 会被拒绝。 |
| `bool` | boolean 或常见 true/false strings | 接受 `true`、`yes`、`y`、`1`、`on`、`false`、`no`、`n`、`0`、`off`。 |
| `choice` | `choices` 中的 string | 必须声明 `choices`；非空 default 必须在 choices 内。 |
| `path` | scalar value | 存为 text。具体 path validation 由 package 自己负责。 |
| `secret` | scalar value | 使用时可写入 installed config，但在 `packages.yaml` 中脱敏。 |

`choices` entries 可以是 strings：

```yaml
choices:
  - direct
  - summary
```

也可以是带描述的 objects：

```yaml
choices:
  - value: direct
    description: Generate speech from the assistant reply as-is.
  - value: summary
    description: Summarize the assistant reply before generating speech.
```

## Option References

`config` 和 `config_when.config` 可以引用 options：

```yaml
config:
  api_key: ${options.api_key}
  notice: ${options.notice}
```

精确 string reference 会保留 resolved value type：

```yaml
notice: ${options.notice}
```

如果 `notice` 是 `bool`，渲染后的 config value 也是 boolean。

嵌入在更长 string 中的 option reference 会渲染为 text：

```yaml
label: "voice-${options.voice}"
```

如果 option 解析为 `null`，精确 reference 会渲染为 `null`；嵌入式 reference 会渲染为空字符串。

## Component Kinds

| `kind` | Source path | 默认 target | 必需 source files | 安装内容 |
| --- | --- | --- | --- | --- |
| `bootstrap` | `bootstrap/<source>/` | `agent/bootstrap/<source-name>` | `slot.yaml` | Bootstrap slot 和 pipeline entry。 |
| `input` | `input/<source>/` | `agent/input/<source-name>` | `slot.yaml` | Input slot 和 pipeline entry。 |
| `output` | `output/<source>/` | `agent/output/<source-name>` | `slot.yaml` | Output slot 和 pipeline entry。 |
| `tool` | `tool/<source>/` | `agent/tools/<source-name>` | `tool.yaml` | Authored tool directory。 |
| `skill` | `skill/<source>/` | `agent/skills/<source-name>` | None enforced | Skill directory。 |
| `lib` | `lib/<source>/` | `agent/lib/<source-name>` | None enforced | Package-owned helper code 或 config。 |
| `core` | `core/<source>/` | `target_core_id` 或 component `id` 命名的 runtime core | install time 期望 `agent.yaml` | Package-owned runtime child core。 |
| `mcp` | `mcp/<source>` | target core MCP declaration root 加 source filename | YAML file | 一个 MCP server declaration。 |
| `schedule` | `schedule/<source>` | target core schedule declaration root 加 source filename | YAML file | 一个 schedule declaration。 |

Components 按稳定 kind order 安装：

```text
lib, bootstrap, input, output, tool, skill, core, mcp, schedule
```

这让 slots 和 tools 在 runtime 加载 installed core 时可以 import package-owned `lib` files。

## Component 字段

| 字段 | 是否必需 | 适用范围 | 类型 | 默认值 | 含义 |
| --- | --- | --- | --- | --- | --- |
| `id` | 是 | all components | string | None | Component id，在 recipe 内唯一。 |
| `kind` | 是 | all components | string | None | 支持的 component kind 之一。 |
| `source` | 是 | all components | string | None | Matching repository root 下的 source name。必须留在 repository 内。 |
| `target` | 否 | all except `core` | string | kind-specific default | Runtime-core-relative target path。 |
| `target_core_id` | 否 | `core` | string | component `id` | 要创建或更新的 runtime core id。 |
| `pipeline` | 对 `bootstrap`、`input`、`output` 必需；对其他 kind 无效 | slot components | mapping | None | Pipeline group 和 placement。 |
| `config` | 否 | all except `core` | mapping | None | 用 options 渲染的 config overlay。 |
| `when` | 否 | all components | mapping | `{}` | 包含或跳过 component 的 option condition。 |
| `config_when` | 否 | all except `core` | list | `[]` | Conditional config overlay list。 |

`source` 不能是 absolute path，不能包含 `..`，不能是 symlink，也不能包含 symlinks。

## Pipeline Placement

只有 `bootstrap`、`input` 和 `output` components 可以编辑 `agent/pipelines.yaml`。

每个 slot component 都必须声明一个 pipeline group，并且只能声明一种 placement：

```yaml
pipeline:
  group: serial
  append: true
```

```yaml
pipeline:
  group: serial
  before: base_input
```

```yaml
pipeline:
  group: parallel
  after: artifact_writer
```

支持的 groups：

| Component kind | Groups |
| --- | --- |
| `bootstrap` | `serial` |
| `input` | `serial`, `parallel` |
| `output` | `serial`, `parallel` |

规则：

- Pipeline mapping 只能包含 `group`、`append`、`before` 和 `after`。
- `append`、`before`、`after` 必须且只能有一个生效。
- `before` 和 `after` targets 必须已经存在于 target pipeline。
- 如果 target pipeline 已经包含该 slot id，install 会失败。
- Uninstall 会移除 package-owned pipeline entries。

Installed slot id 是 installed target directory name。

## Config Overlays

对于目录 components，`config` 是对 source component 的 `config.yaml` 的 deep-merge overlay。

如果目录 component 声明了 `config` 或 `config_when`，它的 source directory 必须包含 `config.yaml`。

```yaml
components:
  - id: stt_lib
    kind: lib
    source: stt_openai
    target: agent/lib/stt_openai
    config:
      api_key: ${options.api_key}
      language: ${options.language}
```

Merge behavior：

- Mapping values 会递归 merge。
- Scalars、lists 和 `null` 会替换 source value。
- Rendered effective config 会写回 installed component 的 `config.yaml`。
- Install record 保存 config hash，而不是完整 effective config。

对于 `mcp` 和 `schedule`，`config` 会在 schema validation 和 normalization 前直接作为 manifest overlay 应用。

`core` components 不能声明 `config` 或 `config_when`。

## Conditions

使用 `when` 包含或跳过整个 component：

```yaml
components:
  - id: tts_tool
    kind: tool
    source: text_to_speech_minimax
    target: agent/tools/text_to_speech
    when:
      enable_tool: true
```

使用 `config_when` 只在 condition match 时应用额外 config：

```yaml
components:
  - id: tts_output
    kind: output
    source: tts_minimax
    target: agent/output/tts_minimax
    pipeline:
      group: parallel
      append: true
    config_when:
      - when:
          mode: summary
        config:
          summarizer_core: tts_summarizer
```

Condition 规则：

- Conditions 是从 option id 到 expected value 的 mappings。
- 每个 referenced option id 都必须存在于 `options`。
- 匹配发生在 option values 已 resolved 和 normalized 之后，且必须精确匹配。
- Empty 或 missing `when` 表示 component 或 config overlay 始终适用。

## Manifest File Components

`mcp` 和 `schedule` components 使用一个 YAML source file，并把一个 rendered YAML file 安装到 target core 的 declaration root。

默认 roots 来自 target core 的 `agent.yaml`：

| Kind | Slot name | Fallback root |
| --- | --- | --- |
| `mcp` | `slots.mcp` | `agent/mcp` |
| `schedule` | `slots.schedules` | `agent/schedules` |

规则：

- Source files 位于 `mcp/` 或 `schedule/`。
- Source files 必须使用 `.yaml` 或 `.yml`。
- Targets 必须是 YAML files。
- Targets 必须直接位于 declaration root 内。
- 同一 manifest id、不同 YAML suffix 的 sibling file 会产生冲突。
- Installed files 会通过 MCP 或 schedule manifest schema normalization。

示例：

```yaml
components:
  - id: docs_mcp
    kind: mcp
    source: docs.yaml
    config:
      url: ${options.url}
  - id: weekday_summary
    kind: schedule
    source: weekday_summary.yaml
    config:
      prompt: ${options.prompt}
```

MCP 和 schedule packages 只安装 declarations。Host 仍然拥有 MCP transport、server lifecycle、schedule claims、approvals 和 execution。

## Component Source Manifests

Recipe YAML 不替代 component manifests。

Slot component source directories 必须包含 `slot.yaml`。允许的 keys 是：

```text
entrypoint
description
input_schema
capabilities
timeout_seconds
failure_policy
default_placement
history_policy
```

Tool component source directories 必须包含 `tool.yaml`。允许的 keys 是：

```text
entrypoint
description
input_schema
risk
capability
approval_policy
display_policy
model_output_policy
capabilities
```

Repository 加载时，`slot.yaml` 或 `tool.yaml` 中的 unknown keys 会被拒绝。

## 复用和冲突

Install 会拒绝 unmanaged target conflicts。

只有当已有 installed package 拥有相同 repository alias、source、target 和 effective config hash 时，多个 packages 才能复用同一个 component target。

Install 也会拒绝：

- 一个 repository 内重复的 package ids。
- 一个 recipe 内重复的 component ids。
- 一个 install plan 内重复的 targets。
- 一个 declaration root 内重复的 MCP 或 schedule manifest ids。
- Shared component config conflicts。

Uninstall 会移除 package-owned targets 和 package-owned pipeline entries。它不会移除写在 package-owned targets 之外的数据。

## Validation Rules

- `repository.yaml` 和 recipe `schema_version` 必须是 `1`。
- `tags`、`manual_dependencies` 和 `capabilities` 必须是 string lists。
- `components` 必须是 mapping list。
- Component `kind` 必须受支持。
- Component `id`、`kind` 和 `source` 必需。
- `bootstrap`、`input` 和 `output` 必须声明 `pipeline`。
- `pipeline` 对其他 component kinds 无效。
- `config` 存在时必须是 mapping。
- `config_when` 必须是包含 `config` mapping 的 object list。
- `core` components 不能使用 `config` 或 `config_when`。
- `when` 和 `config_when.when` 必须引用已声明 options。
- Component sources 必须留在 package repository 内。
- Component sources 不能是 symlinks，也不能包含 symlinks。
- Package recipes 不会安装 Python dependencies。
- Package recipes 不会编辑 host dependency files。
