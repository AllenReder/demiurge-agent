---
title: Package Recipe 参考
description: package repository recipes 和 components 的参考说明。
---

# Package Recipe 参考

Package recipes 位于：

```text
packages/<package_id>.yaml
```

它们描述要安装进 runtime Agent Cores 的 files。

Recipes 可以把 Agent Slots 与 tools、skills、libraries 和 child cores 组合起来。package 是 distribution unit；slot 是 agent loop 内受治理的交互边界。

## Recipe 形状

```yaml
schema_version: 2
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
      before: base_input
    config:
      tone: ${options.tone}
```

## 顶层字段

| Field | Meaning |
| --- | --- |
| `schema_version` | Recipe schema 版本。当前内置 recipes 使用 `2`。 |
| `id` | package id。必须在 repository 内唯一。 |
| `name` | 显示名称。 |
| `summary` | 简短的 package 概要。 |
| `tags` | 字符串标签列表。 |
| `manual_dependencies` | Demiurge 不会安装的依赖警告字符串。 |
| `options` | 用户在安装时提供的选项。 |
| `components` | 要安装的 files 或 cores。 |

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

## Component Fields

| Field | Meaning |
| --- | --- |
| `id` | component id，在 recipe 内唯一。 |
| `kind` | component kind。 |
| `source` | repository-relative 的 component source id。 |
| `target` | core-local kinds 使用的 runtime-core-relative target path。 |
| `target_core_id` | `kind: core` 的目标 core id。 |
| `pipeline` | 用于 bootstrap/input/output components 的 pipeline edit。 |
| `config` | 写入已安装 component 的 config。 |
| `when` | 决定包含或跳过该 component 的 option condition。 |
| `config_when` | 条件式 config merge list。 |

## Conditions and Config

`when` 会把 option ids 映射到期望值。`config_when` 在条件匹配时合并额外 config。

Config values 可以直接引用 option：

```yaml
api_key: ${options.api_key}
```

也可以在支持的情况下嵌入字符串中。

## Validation Rules

- Component sources 必须留在 package repository 内。
- Component sources 不能是 symlinks。
- 现有 targets 会被拒绝，除非它们正被另一个已安装 package 以相同 source 和 target 复用。
- Pipeline edits 只允许用于 `bootstrap`、`input` 和 `output`。
- Bootstrap pipeline edits 只能是 serial。
- Recipes 不会安装 Python dependencies，也不会编辑 host lock file。
