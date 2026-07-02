---
title: Package Recipe Reference
description: Reference for package repository recipes and components.
---

# Package Recipe Reference

Package recipes live under:

```text
packages/<package_id>.yaml
```

They describe files to install into runtime Agent Cores.

Recipes can combine Agent Slots with tools, skills, libraries, and child cores.
The package is the distribution unit; the slot is the governed interaction
boundary inside the agent loop.

## Recipe Shape

```yaml
schema_version: 3
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
config_defaults: {}
capabilities: []
slots:
  - id: reply_style_input
    phase: input
    source: reply_style
    target: agent/input/reply_style
    metadata:
      failure: soft
      capabilities: []
      description: "Adds a reply style input module."
    pipeline:
      before: base_input
    config:
      tone: ${options.tone}
tools: []
files: []
```

## Top-Level Fields

| Field | Meaning |
| --- | --- |
| `schema_version` | Recipe schema version. Current recipes use `3`. |
| `id` | Package id. Must be unique in the repository. |
| `name` | Display name. |
| `summary` | Short package summary. |
| `tags` | List of string tags. |
| `manual_dependencies` | Warning strings for dependencies Demiurge will not install. |
| `options` | User-provided install options. |
| `config_defaults` | Optional config fragments merged into matching entries. |
| `capabilities` | Package-level capability summary for review. |
| `slots` | Bootstrap/input/output slot entries to install. |
| `tools` | Authored tool entries to install. |
| `files` | Libraries, skills, child cores, MCP declarations, and schedules. |

## Option Types

Supported option types:

- `string`
- `bool`
- `choice`
- `path`
- `secret`

`choice` options require `choices`. Secret values are redacted in
`packages.yaml`.

## Entry Kinds

| Section | Kind field | Default target root |
| --- | --- |
| `slots` | `phase: bootstrap` | `agent/bootstrap` |
| `slots` | `phase: input` | `agent/input` |
| `slots` | `phase: output` | `agent/output` |
| `tools` | implicit `tool` | `agent/tools` |
| `files` | `kind: skill` | `agent/skills` |
| `files` | `kind: lib` | `agent/lib` |
| `files` | `kind: core` | Another runtime core by `target_core_id`. |
| `files` | `kind: mcp` | The target core's MCP declaration root. |
| `files` | `kind: schedule` | The target core's schedule declaration root. |

## Entry Fields

| Field | Meaning |
| --- | --- |
| `id` | Entry id, unique within the recipe. |
| `phase` | Slot phase for `slots` entries. |
| `kind` | File kind for `files` entries. |
| `source` | Repository-relative component source id. |
| `target` | Runtime-core-relative target path for core-local kinds. |
| `target_core_id` | Target core id for `kind: core`. |
| `metadata` | Slot or tool metadata written to `agent/slots.yaml` or `tool.yaml`. |
| `pipeline` | Pipeline edit for bootstrap/input/output slot entries. |
| `config` | Config written into the installed component. |
| `when` | Option condition that includes or skips the component. |
| `config_when` | Conditional config merge list. |

## Conditions and Config

`when` maps option ids to expected values. `config_when` merges extra config
when its condition matches.

Config values can reference an option exactly:

```yaml
api_key: ${options.api_key}
```

or inside strings where supported by the installer.

## Validation Rules

- Component sources must stay inside the package repository.
- Component sources cannot be symlinks.
- Existing targets are rejected unless reused by another installed package with
  the same source and target.
- Pipeline edits are allowed only for `bootstrap`, `input`, and `output`.
- Bootstrap pipeline edits are serial-only.
- Recipes do not install Python dependencies or edit the host lock file.
