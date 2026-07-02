---
title: 包配方参考
description: Package repository recipes、options、components、pipeline placement、config overlays 和 validation rules 的参考。
---

# 包配方参考

Package recipes 位于 package repository 的：

```text
packages/<package_id>.yaml
```

它们描述要安装进 runtime Agent Cores 的文件。Component directories 拥有 code、manifests、默认 `config.yaml`，以及 slot 或 tool metadata。Recipe 拥有 package identity、install options、conditions、target paths、pipeline placement 和 config overlays。

## 示例

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

## 顶层字段

| 字段 | 是否必需 | 含义 |
| --- | --- | --- |
| `schema_version` | 是 | Recipe schema version。当前 recipes 使用 `1`。 |
| `id` | 是 | Package id。必须在 repository 内唯一。 |
| `name` | 否 | 显示名称。默认是 `id`。 |
| `summary` | 否 | 简短 package summary。 |
| `tags` | 否 | 用于 browse 和 filter flows 的字符串 tag 列表。 |
| `manual_dependencies` | 否 | Demiurge 不会安装的 dependencies warning strings。 |
| `options` | 否 | 用户提供的 install options。 |
| `capabilities` | 否 | 供 review 使用的 package-level capability summary。 |
| `components` | 是 | 要安装的 component entries。 |

已移除的字段会被拒绝，例如 `slots`、`tools`、`files`、`config_defaults` 和 package-level `metadata`。

## 选项

支持的 option types：

| 类型 | 含义 |
| --- | --- |
| `string` | Free-form text。 |
| `bool` | Boolean。CLI values 接受常见 true/false forms。 |
| `choice` | 从 `choices` 中选择一个 value。 |
| `path` | Path-like text。 |
| `secret` | Secret text。会在 `packages.yaml` 中脱敏。 |

常见 option fields：

| 字段 | 含义 |
| --- | --- |
| `id` | `${options.<id>}` references 使用的 option id。 |
| `type` | 支持的 option types 之一。 |
| `prompt` | 交互式 manager 使用的 label。 |
| `description` | 交互式 manager 和文档展示的 help text。 |
| `default` | 默认值。 |
| `required` | 空值是否会被拒绝。 |
| `choices` | `choice` 必需。Entries 可以是 strings，也可以是 `{value, description}` objects。 |

脚本化安装用重复 flags 传入 options：

```bash
uv run demiurge package install tts_minimax \
  --core assistant \
  --option mode=summary \
  --option enable_tool=true
```

## Component 类型

| `kind` | Source | 默认 target |
| --- | --- | --- |
| `bootstrap` | `bootstrap/<source>/` | `agent/bootstrap/<source>` |
| `input` | `input/<source>/` | `agent/input/<source>` |
| `output` | `output/<source>/` | `agent/output/<source>` |
| `tool` | `tool/<source>/` | `agent/tools/<source>` |
| `skill` | `skill/<source>/` | `agent/skills/<source>` |
| `lib` | `lib/<source>/` | `agent/lib/<source>` |
| `core` | `core/<source>/` | Runtime core named by `target_core_id`. |
| `mcp` | `mcp/<source>` | Target core MCP declaration root. |
| `schedule` | `schedule/<source>` | Target core schedule declaration root. |

`bootstrap`、`input` 和 `output` sources 必须包含 `slot.yaml`。`tool` sources 必须包含 `tool.yaml`。

`mcp` 和 `schedule` sources 是 YAML manifest files。它们只安装 declaration files；host 拥有 MCP transport 和 schedule execution。

## Component 字段

| 字段 | 是否必需 | 含义 |
| --- | --- | --- |
| `id` | 是 | Component id，在 recipe 内唯一。 |
| `kind` | 是 | Component kind。 |
| `source` | 是 | 匹配 repository root 下的 source name。 |
| `target` | 否 | Runtime-core-relative target path。 |
| `target_core_id` | For `core` when not using the component id | `kind: core` 的 runtime core id。 |
| `pipeline` | 对 `bootstrap`、`input` 和 `output` 必需 | Pipeline placement。 |
| `config` | 否 | 用 package options 渲染的 config overlay。 |
| `when` | 否 | 包含或跳过该 component 的 option condition。 |
| `config_when` | 否 | Conditional config overlay list。 |

Components 以稳定的 kind order 安装：`lib`、`bootstrap`、`input`、`output`、`tool`、`skill`、`core`、`mcp`，然后是 `schedule`。

## Pipeline 放置

只有 `bootstrap`、`input` 和 `output` components 可以编辑 pipelines。

每次 pipeline edit 都要声明一个 group，并且只能声明一种 placement：

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

Bootstrap 只支持 `group: serial`。Input 和 output 支持 `serial` 和 `parallel`。

安装期间，Demiurge 会拒绝缺失的 `before` 或 `after` targets，并拒绝 target pipeline 中已存在的重复 slot ids。

## Config 覆盖

对于目录 components，`config` 是对 source component 的 `config.yaml` 的 deep-merge overlay。如果 component 声明了 `config` 或 `config_when`，source directory 必须包含 `config.yaml`。

Mapping values 会递归 merge。Scalars、lists 和 `null` 会替换 source value。

```yaml
config:
  api_key: ${options.api_key}
  limits:
    memory_chars: 2200
```

精确的 string option references 会保留解析后的 value type：

```yaml
notice: ${options.notice}
```

嵌入在更长字符串中的 option references 会渲染为 text。

`mcp` 和 `schedule` components 会在 validation 前，把 `config` 直接作为 manifest overlay 应用。

## 条件

使用 `when` 包含或跳过 component：

```yaml
when:
  enable_tool: true
```

使用 `config_when` 应用条件式 config overlays：

```yaml
config_when:
  - when:
      mode: summary
    config:
      summarizer_core: tts_summarizer
```

Conditions 会精确匹配 resolved option values。

## Manifest 文件组件

MCP 和 schedule components 使用一个 source YAML file，并把一个 rendered YAML file 安装到 target core 的 declaration root。

默认 root 来自 `agent.yaml`：

| 类型 | Slot name | Fallback root |
| --- | --- | --- |
| `mcp` | `slots.mcp` | `agent/mcp` |
| `schedule` | `slots.schedules` | `agent/schedules` |

Manifest targets 必须是 declaration root 直接子级中的 YAML files。

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

安装的 files 是 package-owned targets，可供 uninstall 移除。启动 MCP server、claim schedule、检查 approvals 和执行 scheduled turns 仍然是 host responsibilities。

## 复用和冲突

只有当现有 installed component 具有相同 repository alias、source、target 和 effective config hash 时，多个 packages 才能复用同一个 component target。

如果 unmanaged file 已存在于 target，installation 会失败。如果一个 package 中两个 components target 同一路径，installation 也会失败。

Installed `packages.yaml` 保存 redacted options、component records、warnings 和 config hashes。它不会保存完整 effective config 或 secrets。

## 验证规则

- Component sources 必须留在 package repository 内。
- Component sources 不能是 symlinks。
- Package ids 在 repository 内唯一。
- Component ids 在 recipe 内唯一。
- Unknown component kinds 会被拒绝。
- Pipeline edits 只允许用于 `bootstrap`、`input` 和 `output`。
- Bootstrap pipeline edits 只能是 serial。
- `mcp` 和 `schedule` targets 必须是 declaration root 直接子级中的 YAML files。
- Package recipes 不会安装 Python dependencies。
- Package recipes 不会编辑 host `uv.lock`。
