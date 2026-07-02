---
title: Package Recipe Reference
description: Reference for package repository recipes and components.
---

# Package Recipe Reference

Package recipes live under:

```text
packages/<package_id>.yaml
```

They describe reusable components to copy into runtime Agent Cores. The package
recipe owns assembly, options, conditions, targets, pipeline placement, and
config overrides. Component directories own code, default `config.yaml`, and
their `slot.yaml` or `tool.yaml` manifest.

## Recipe Shape

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

## Top-Level Fields

| Field | Meaning |
| --- | --- |
| `schema_version` | Recipe schema version. Current recipes use `1`. |
| `id` | Package id. Must be unique in the repository. |
| `name` | Display name. |
| `summary` | Short package summary. |
| `tags` | List of string tags. |
| `manual_dependencies` | Warning strings for dependencies Demiurge will not install. |
| `options` | User-provided install options. |
| `capabilities` | Package-level capability summary for review. |
| `components` | Ordered component entries to install. |

Removed fields such as `slots`, `tools`, `files`, `config_defaults`, and
package-level `metadata` are rejected.

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

| `kind` | Source root | Default target |
| --- | --- | --- |
| `bootstrap` | `bootstrap/<source>/` | `agent/bootstrap/<source>` |
| `input` | `input/<source>/` | `agent/input/<source>` |
| `output` | `output/<source>/` | `agent/output/<source>` |
| `tool` | `tool/<source>/` | `agent/tools/<source>` |
| `skill` | `skill/<source>/` | `agent/skills/<source>` |
| `lib` | `lib/<source>/` | `agent/lib/<source>` |
| `core` | `core/<source>/` | Another runtime core by `target_core_id`. |
| `mcp` | `mcp/<source>.yaml` | The target core's MCP declaration root. |
| `schedule` | `schedule/<source>.yaml` | The target core's schedule declaration root. |

`bootstrap`, `input`, and `output` sources must contain `slot.yaml`. `tool`
sources must contain `tool.yaml`. Unknown fields in those manifests are
rejected.

## Component Fields

| Field | Meaning |
| --- | --- |
| `id` | Component id, unique within the recipe. |
| `kind` | Component kind. |
| `source` | Source name under the matching repository root. |
| `target` | Optional runtime-core-relative target path. |
| `target_core_id` | Target core id for `kind: core`. |
| `pipeline` | Required placement for `bootstrap`, `input`, and `output`. |
| `config` | Deep-merge patch applied to the component source `config.yaml`. |
| `when` | Option condition that includes or skips the component. |
| `config_when` | Conditional config merge list. |

## Pipeline Placement

Slot components must declare the target pipeline group and exactly one
placement:

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

Bootstrap supports only `group: serial`. Input and output support `serial` and
`parallel`.

## Config

For directory components, `config` is a patch over the source component's
`config.yaml`. If a component uses `config` or `config_when`, the source
directory must contain `config.yaml`.

Mapping values merge recursively. Scalars, lists, and `null` replace the source
value.

```yaml
config:
  api_key: ${options.api_key}
  limits:
    memory_chars: 2200
```

`mcp` and `schedule` components are YAML manifest components. Their `config`
patch applies directly to the source manifest before validation instead of using
a sibling `config.yaml`.

## Reuse

Multiple packages may reuse the same `repository/kind/source/target` component.
The final effective config hash must match. If two packages would install the
same shared component with different effective config, installation fails.

Installed `packages.yaml` stores redacted options, component records, and
`config_hash`; it does not store complete effective config or secrets.

## Validation Rules

- Component sources must stay inside the package repository.
- Component sources cannot be symlinks.
- Existing targets are rejected unless reused by another installed package with
  the same repository alias, kind, source, target, and config hash.
- Pipeline edits are allowed only for `bootstrap`, `input`, and `output`.
- Bootstrap pipeline edits are serial-only.
- Recipes do not install Python dependencies or edit the host lock file.
