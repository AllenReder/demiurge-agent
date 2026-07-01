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

## Top-Level Fields

| Field | Meaning |
| --- | --- |
| `schema_version` | Recipe schema version. Current built-in recipes use `2`. |
| `id` | Package id. Must be unique in the repository. |
| `name` | Display name. |
| `summary` | Short package summary. |
| `tags` | List of string tags. |
| `manual_dependencies` | Warning strings for dependencies Demiurge will not install. |
| `options` | User-provided install options. |
| `components` | Files or cores to install. |

## Option Types

Supported option types:

- `string`
- `bool`
- `choice`
- `path`
- `secret`

`choice` options require `choices`. Secret values are redacted in
`packages.yaml`.

## Component Kinds

| Kind | Default target root |
| --- | --- |
| `bootstrap` | `agent/bootstrap` |
| `input` | `agent/input` |
| `output` | `agent/output` |
| `tool` | `agent/tools` |
| `skill` | `agent/skills` |
| `lib` | `agent/lib` |
| `core` | Another runtime core by `target_core_id`. |

## Component Fields

| Field | Meaning |
| --- | --- |
| `id` | Component id, unique within the recipe. |
| `kind` | Component kind. |
| `source` | Repository-relative component source id. |
| `target` | Runtime-core-relative target path for core-local kinds. |
| `target_core_id` | Target core id for `kind: core`. |
| `pipeline` | Pipeline edit for bootstrap/input/output components. |
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
