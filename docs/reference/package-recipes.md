---
title: Package Recipe Reference
description: Reference for package repository recipes, options, components, pipeline placement, config overlays, and validation rules.
---

# Package Recipe Reference

Package recipes live in a package repository under:

```text
packages/<package_id>.yaml
```

They describe files to install into runtime Agent Cores. Component directories
own code, manifests, default `config.yaml`, and slot or tool metadata. The
recipe owns package identity, install options, conditions, target paths,
pipeline placement, and config overlays.

## Example

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

## Top-Level Fields

| Field | Required | Meaning |
| --- | --- | --- |
| `schema_version` | Yes | Recipe schema version. Current recipes use `1`. |
| `id` | Yes | Package id. Must be unique within the repository. |
| `name` | No | Display name. Defaults to `id`. |
| `summary` | No | Short package summary. |
| `tags` | No | List of string tags for browse and filter flows. |
| `manual_dependencies` | No | Warning strings for dependencies Demiurge will not install. |
| `options` | No | User-provided install options. |
| `capabilities` | No | Package-level capability summary for review. |
| `components` | Yes | Component entries to install. |

Removed fields such as `slots`, `tools`, `files`, `config_defaults`, and
package-level `metadata` are rejected.

## Options

Supported option types:

| Type | Meaning |
| --- | --- |
| `string` | Free-form text. |
| `bool` | Boolean. CLI values accept common true/false forms. |
| `choice` | One value from `choices`. |
| `path` | Path-like text. |
| `secret` | Secret text. Redacted in `packages.yaml`. |

Common option fields:

| Field | Meaning |
| --- | --- |
| `id` | Option id used by `${options.<id>}` references. |
| `type` | One of the supported option types. |
| `prompt` | Label used by the interactive manager. |
| `description` | Help text shown by the interactive manager and documentation. |
| `default` | Default value. |
| `required` | Whether an empty value is rejected. |
| `choices` | Required for `choice`. Entries can be strings or `{value, description}` objects. |

Scripted installs pass options with repeated flags:

```bash
uv run demiurge package install tts_minimax \
  --core assistant \
  --option mode=summary \
  --option enable_tool=true
```

## Component Kinds

| `kind` | Source | Default target |
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

`bootstrap`, `input`, and `output` sources must contain `slot.yaml`. `tool`
sources must contain `tool.yaml`.

`mcp` and `schedule` sources are YAML manifest files. They install declaration
files only; the host owns MCP transport and schedule execution.

## Component Fields

| Field | Required | Meaning |
| --- | --- | --- |
| `id` | Yes | Component id, unique within the recipe. |
| `kind` | Yes | Component kind. |
| `source` | Yes | Source name under the matching repository root. |
| `target` | No | Runtime-core-relative target path. |
| `target_core_id` | For `core` when not using the component id | Runtime core id for `kind: core`. |
| `pipeline` | For `bootstrap`, `input`, and `output` | Pipeline placement. |
| `config` | No | Config overlay rendered with package options. |
| `when` | No | Option condition that includes or skips the component. |
| `config_when` | No | Conditional config overlay list. |

Components are installed in a stable kind order: `lib`, `bootstrap`, `input`,
`output`, `tool`, `skill`, `core`, `mcp`, then `schedule`.

## Pipeline Placement

Only `bootstrap`, `input`, and `output` components can edit pipelines.

Every pipeline edit declares a group and exactly one placement:

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

During installation, Demiurge rejects missing `before` or `after` targets and
rejects duplicate slot ids already present in the target pipeline.

## Config Overlays

For directory components, `config` is a deep-merge overlay on the source
component's `config.yaml`. If a component declares `config` or `config_when`,
the source directory must contain `config.yaml`.

Mapping values merge recursively. Scalars, lists, and `null` replace the source
value.

```yaml
config:
  api_key: ${options.api_key}
  limits:
    memory_chars: 2200
```

Exact string option references preserve the resolved value type:

```yaml
notice: ${options.notice}
```

Option references embedded inside longer strings render as text.

`mcp` and `schedule` components apply `config` directly as a manifest overlay
before validation.

## Conditions

Use `when` to include or skip a component:

```yaml
when:
  enable_tool: true
```

Use `config_when` to apply conditional config overlays:

```yaml
config_when:
  - when:
      mode: summary
    config:
      summarizer_core: tts_summarizer
```

Conditions are exact matches against resolved option values.

## Manifest File Components

MCP and schedule components use one source YAML file and install one rendered
YAML file into the target core's declaration root.

Default roots come from `agent.yaml`:

| Kind | Slot name | Fallback root |
| --- | --- | --- |
| `mcp` | `slots.mcp` | `agent/mcp` |
| `schedule` | `slots.schedules` | `agent/schedules` |

Manifest targets must be YAML files directly inside the declaration root.

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

The installed files are package-owned targets for uninstall. Starting an MCP
server, claiming a schedule, checking approvals, and executing scheduled turns
remain host responsibilities.

## Reuse and Conflicts

Multiple packages can reuse the same component target only when the existing
installed component has the same repository alias, source, target, and effective
config hash.

If an unmanaged file already exists at the target, installation fails. If two
components in one package target the same path, installation fails.

Installed `packages.yaml` stores redacted options, component records, warnings,
and config hashes. It does not store full effective config or secrets.

## Validation Rules

- Component sources must stay inside the package repository.
- Component sources cannot be symlinks.
- Package ids are unique within a repository.
- Component ids are unique within a recipe.
- Unknown component kinds are rejected.
- Pipeline edits are allowed only for `bootstrap`, `input`, and `output`.
- Bootstrap pipeline edits are serial-only.
- `mcp` and `schedule` targets must be YAML files directly inside their
  declaration root.
- Package recipes do not install Python dependencies.
- Package recipes do not edit the host `uv.lock`.
