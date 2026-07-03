---
title: Package Recipe Reference
description: Complete reference for package recipe YAML fields, options, components, config overlays, conditions, and validation rules.
---

# Package Recipe Reference

Package recipes live in a package repository under:

```text
packages/<package_id>.yaml
```

A recipe describes which authored-surface files to install into runtime Agent
Cores. It owns package identity, install-time options, component selection,
target paths, pipeline placement, conditions, and config overlays.

Component source directories own runtime files such as `slot.yaml`, `tool.yaml`,
`config.yaml`, Python modules, skills, libraries, child cores, MCP manifests,
and schedule manifests.

For a task-oriented writing guide, see
[Write a Package Recipe](../how-to/write-package-recipe.md).

## Complete Shape

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

Only `schema_version`, `id`, and `components` are required at the top level.

## Top-Level Fields

| Field | Required | Type | Default | Meaning |
| --- | --- | --- | --- | --- |
| `schema_version` | Yes | integer | None | Must be `1`. |
| `id` | Yes | string | None | Package id, unique within the repository. |
| `name` | No | string | `id` | Display name in package lists and interactive flows. |
| `summary` | No | string | empty string | Short package summary. |
| `tags` | No | list of strings | `[]` | Filter and browse tags such as `memory`, `stt`, or `provider:openai`. |
| `manual_dependencies` | No | list of strings | `[]` | Human-review warnings for dependencies Demiurge will not install. |
| `options` | No | list of option objects | `[]` | Install-time values that users can provide. |
| `capabilities` | No | list of strings | `[]` | Package-level capability summary for review. |
| `components` | Yes | list of component objects | None | Components to install. |

Removed v1 fields are rejected:

```text
slots
tools
files
config_defaults
metadata
```

Component-level `metadata` and `phase` are also rejected.

## Option Fields

Options are resolved once during install or preview. Unknown script-supplied
options are rejected.

| Field | Required | Type | Default | Meaning |
| --- | --- | --- | --- | --- |
| `id` | Yes | string | None | Option id used by `${options.<id>}` references. Must be unique in the recipe. |
| `type` | No | string | `string` | One of `string`, `bool`, `choice`, `path`, or `secret`. |
| `prompt` | No | string | `id` | Label shown by the interactive package manager. |
| `description` | No | string | empty string | Help text shown in interactive flows and docs. |
| `default` | No | any scalar | `null` when absent | Value used when the user does not provide an answer. |
| `required` | No | boolean | `false` | Reject missing or empty values after defaults are applied. |
| `choices` | For `choice` | list | `[]` | Allowed values for a `choice` option. |
| `secret` | No | boolean | `true` for `type: secret` | Redact this option in installed `packages.yaml`. |

Supported option types:

| Type | Accepted install value | Notes |
| --- | --- | --- |
| `string` | scalar value | Stored as text. Lists and maps are rejected. |
| `bool` | boolean or common true/false strings | Accepted strings include `true`, `yes`, `y`, `1`, `on`, `false`, `no`, `n`, `0`, and `off`. |
| `choice` | string from `choices` | `choices` are required; a non-empty default must be one of them. |
| `path` | scalar value | Stored as text. Validation is package-owned. |
| `secret` | scalar value | Stored as text in installed config when used, but redacted in `packages.yaml`. |

`choices` entries can be strings:

```yaml
choices:
  - direct
  - summary
```

They can also be objects with descriptions:

```yaml
choices:
  - value: direct
    description: Generate speech from the assistant reply as-is.
  - value: summary
    description: Summarize the assistant reply before generating speech.
```

## Option References

`config` and `config_when.config` can reference options:

```yaml
config:
  api_key: ${options.api_key}
  notice: ${options.notice}
```

An exact string reference preserves the resolved value type:

```yaml
notice: ${options.notice}
```

If `notice` is a `bool`, the rendered config value is a boolean.

An option reference embedded in a longer string renders as text:

```yaml
label: "voice-${options.voice}"
```

If an option resolves to `null`, an exact reference renders `null`; an embedded
reference renders an empty string.

## Component Kinds

| `kind` | Source path | Default target | Required source files | What it installs |
| --- | --- | --- | --- | --- |
| `bootstrap` | `bootstrap/<source>/` | `agent/bootstrap/<source-name>` | `slot.yaml` | A bootstrap slot and pipeline entry. |
| `input` | `input/<source>/` | `agent/input/<source-name>` | `slot.yaml` | An input slot and pipeline entry. |
| `output` | `output/<source>/` | `agent/output/<source-name>` | `slot.yaml` | An output slot and pipeline entry. |
| `tool` | `tool/<source>/` | `agent/tools/<source-name>` | `tool.yaml` | An authored tool directory. |
| `skill` | `skill/<source>/` | `agent/skills/<source-name>` | None enforced | A skill directory. |
| `lib` | `lib/<source>/` | `agent/lib/<source-name>` | None enforced | Package-owned helper code or config. |
| `core` | `core/<source>/` | runtime core named by `target_core_id` or component `id` | `agent.yaml` expected at install time | A package-owned runtime child core. |
| `mcp` | `mcp/<source>` | target core MCP declaration root plus source filename | YAML file | One MCP server declaration. |
| `schedule` | `schedule/<source>` | target core schedule declaration root plus source filename | YAML file | One schedule declaration. |

Components are installed in this stable kind order:

```text
lib, bootstrap, input, output, tool, skill, core, mcp, schedule
```

This order lets slots and tools import package-owned `lib` files when the
runtime loads the installed core.

## Component Fields

| Field | Required | Applies to | Type | Default | Meaning |
| --- | --- | --- | --- | --- | --- |
| `id` | Yes | all components | string | None | Component id, unique within the recipe. |
| `kind` | Yes | all components | string | None | One of the supported component kinds. |
| `source` | Yes | all components | string | None | Source name under the matching repository root. Must stay inside the repository. |
| `target` | No | all except `core` | string | kind-specific default | Runtime-core-relative target path. |
| `target_core_id` | No | `core` | string | component `id` | Runtime core id to create or update. |
| `pipeline` | Yes for `bootstrap`, `input`, `output`; invalid for others | slot components | mapping | None | Pipeline group and placement. |
| `config` | No | all except `core` | mapping | None | Config overlay rendered with options. |
| `when` | No | all components | mapping | `{}` | Option condition that includes or skips the component. |
| `config_when` | No | all except `core` | list | `[]` | Conditional config overlay list. |

`source` cannot be absolute, cannot contain `..`, cannot be a symlink, and
cannot contain symlinks.

## Pipeline Placement

Only `bootstrap`, `input`, and `output` components can edit
`agent/pipelines.yaml`.

Every slot component must declare one pipeline group and exactly one placement:

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

Supported groups:

| Component kind | Groups |
| --- | --- |
| `bootstrap` | `serial` |
| `input` | `serial`, `parallel` |
| `output` | `serial`, `parallel` |

Rules:

- A pipeline mapping can contain only `group`, `append`, `before`, and `after`.
- Exactly one of `append`, `before`, or `after` must be active.
- `before` and `after` targets must already exist in the target pipeline.
- Install fails if the target pipeline already contains the slot id.
- Uninstall removes package-owned pipeline entries.

The installed slot id is the installed target directory name.

## Config Overlays

For directory components, `config` is a deep-merge overlay on the source
component's `config.yaml`.

If a directory component declares `config` or `config_when`, its source
directory must contain `config.yaml`.

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

Merge behavior:

- Mapping values merge recursively.
- Scalars, lists, and `null` replace the source value.
- The rendered effective config is written back to the installed component's
  `config.yaml`.
- The install record stores a config hash, not the full effective config.

For `mcp` and `schedule`, `config` is applied directly as a manifest overlay
before schema validation and normalization.

`core` components cannot declare `config` or `config_when`.

## Conditions

Use `when` to include or skip a whole component:

```yaml
components:
  - id: tts_tool
    kind: tool
    source: text_to_speech_minimax
    target: agent/tools/text_to_speech
    when:
      enable_tool: true
```

Use `config_when` to apply extra config only when a condition matches:

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

Condition rules:

- Conditions are mappings from option id to expected value.
- Every referenced option id must exist in `options`.
- Matching is exact after option values are resolved and normalized.
- Empty or missing `when` means the component or config overlay always applies.

## Manifest File Components

`mcp` and `schedule` components use one YAML source file and install one
rendered YAML file into the target core's declaration root.

Default roots come from the target core's `agent.yaml`:

| Kind | Slot name | Fallback root |
| --- | --- | --- |
| `mcp` | `slots.mcp` | `agent/mcp` |
| `schedule` | `slots.schedules` | `agent/schedules` |

Rules:

- Source files live under `mcp/` or `schedule/`.
- Source files must use `.yaml` or `.yml`.
- Targets must be YAML files.
- Targets must be directly inside the declaration root.
- A sibling file with the same manifest id and a different YAML suffix is a
  conflict.
- Installed files are normalized through the MCP or schedule manifest schema.

Example:

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

MCP and schedule packages install declarations. The host still owns MCP
transport, server lifecycle, schedule claims, approvals, and execution.

## Component Source Manifests

Recipe YAML does not replace component manifests.

Slot component source directories must include `slot.yaml`. Allowed keys are:

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

Tool component source directories must include `tool.yaml`. Allowed keys are:

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

Unknown keys in `slot.yaml` or `tool.yaml` are rejected when the repository is
loaded.

## Reuse and Conflicts

Install rejects unmanaged target conflicts.

Multiple packages can reuse the same component target only when an installed
package already owns the same repository alias, source, target, and effective
config hash.

Install also rejects:

- Duplicate package ids in one repository.
- Duplicate component ids in one recipe.
- Duplicate targets in one install plan.
- Duplicate MCP or schedule manifest ids in one declaration root.
- Shared component config conflicts.

Uninstall removes package-owned targets and package-owned pipeline entries. It
does not remove data written outside package-owned targets.

## Validation Rules

- `repository.yaml` and recipe `schema_version` must be `1`.
- `tags`, `manual_dependencies`, and `capabilities` must be lists of strings.
- `components` must be a list of mappings.
- Component `kind` must be supported.
- Component `id`, `kind`, and `source` are required.
- `pipeline` is required for `bootstrap`, `input`, and `output`.
- `pipeline` is invalid for all other component kinds.
- `config` must be a mapping when present.
- `config_when` must be a list of objects with `config` mappings.
- `core` components cannot use `config` or `config_when`.
- `when` and `config_when.when` must reference declared options.
- Component sources must stay inside the package repository.
- Component sources cannot be symlinks and cannot contain symlinks.
- Package recipes do not install Python dependencies.
- Package recipes do not edit host dependency files.
