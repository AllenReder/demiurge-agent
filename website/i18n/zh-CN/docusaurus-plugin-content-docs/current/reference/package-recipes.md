---
title: Package Recipe 参考
description: package repository recipes 和 components 的参考说明。
---

# Package Recipe 参考

Package recipes 位于：

```text
packages/<package_id>.yaml
```

它们描述要安装进 runtime Agent Cores 的 components。Recipe 负责装配、
options、conditions、targets、pipeline placement 和 config overrides；
组件目录拥有默认代码、默认 `config.yaml`、`slot.yaml` 或 `tool.yaml`。

Recipes 可以把 Agent Slots 与 tools、skills、libraries 和 child cores 组合起来。package 是 distribution unit；slot 是 agent loop 内受治理的交互边界。

## Recipe 形状

```yaml
schema_version: 1
id: reply_style
name: Reply Style
summary: Add a reply style input module.
tags:
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

| Field | Meaning |
| --- | --- |
| `schema_version` | Recipe schema 版本。当前 recipes 使用 `1`。 |
| `id` | package id。必须在 repository 内唯一。 |
| `name` | 显示名称。 |
| `summary` | 简短的 package 概要。 |
| `tags` | 字符串标签列表。 |
| `manual_dependencies` | Demiurge 不会安装的依赖警告字符串。 |
| `options` | 用户在安装时提供的选项。 |
| `components` | 要安装的 component entries。 |

旧字段 `slots`、`tools`、`files`、`config_defaults` 和 package-level
`metadata` 会被拒绝。

## Option 类型

支持的 option types：

- `string`
- `bool`
- `choice`
- `path`
- `secret`

`choice` options 需要 `choices`。secret values 会在 `packages.yaml` 中被 redacted。

## Component 类型

| Kind | Default target root |
| --- | --- |
| `bootstrap` | `agent/bootstrap` |
| `input` | `agent/input` |
| `output` | `agent/output` |
| `tool` | `agent/tools` |
| `skill` | `agent/skills` |
| `lib` | `agent/lib` |
| `core` | 另一个通过 `target_core_id` 指定的 runtime core。 |
| `mcp` | 目标 core 的 MCP declaration root。 |
| `schedule` | 目标 core 的 schedule declaration root。 |

`bootstrap`、`input` 和 `output` sources 必须包含 `slot.yaml`。`tool`
sources 必须包含 `tool.yaml`。这些 manifest 中的未知字段会被拒绝。

## Component Fields

| Field | Meaning |
| --- | --- |
| `id` | component id，在 recipe 内唯一。 |
| `kind` | component kind。 |
| `source` | repository-relative 的 component source id。 |
| `target` | core-local kinds 使用的 runtime-core-relative target path。 |
| `target_core_id` | `kind: core` 的目标 core id。 |
| `pipeline` | 用于 bootstrap/input/output components 的 pipeline edit。 |
| `config` | 对 source component `config.yaml` 的 deep-merge patch。 |
| `when` | 决定包含或跳过该 component 的 option condition。 |
| `config_when` | 条件式 config merge list。 |

## Conditions and Config

`when` 会把 option ids 映射到期望值。`config_when` 在条件匹配时合并额外 config。

对于目录组件，`config` 会覆盖 source component 内的 `config.yaml`。如果
component 写了 `config` 或 `config_when`，source 目录必须包含 `config.yaml`。
mapping 会递归合并；scalar、list 和 `null` 会替换原值。

Config values 可以直接引用 option：

```yaml
api_key: ${options.api_key}
```

也可以在支持的情况下嵌入字符串中。

## Validation Rules

- Component sources 必须留在 package repository 内。
- Component sources 不能是 symlinks。
- 现有 targets 会被拒绝，除非它们正被另一个已安装 package 以相同 repository、source、target 和 config hash 复用。
- Pipeline edits 只允许用于 `bootstrap`、`input` 和 `output`。
- Bootstrap pipeline edits 只能是 serial。
- Recipes 不会安装 Python dependencies，也不会编辑 host lock file。
