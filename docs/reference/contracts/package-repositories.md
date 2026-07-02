---
title: Package Repository Contract
description: Stable rules for external package repositories and package recipes.
---

# Package Repository Contract

This contract describes the repository shape that Demiurge can inspect, trust,
preview, install, sync, and uninstall.

The implementation source of truth is `demiurge/packages.py`. The built-in
repository is `package-repository/`.

## Repository Root

Required:

```text
repository.yaml
packages/
```

Optional component roots:

```text
bootstrap/
input/
output/
tool/
skill/
lib/
core/
mcp/
schedule/
```

## `repository.yaml`

```yaml
schema_version: 1
id: community
name: Community Packages
summary: Shared Demiurge package recipes.
```

`id` must be stable. Local users may assign a different alias when adding the
repository.

## Package Recipes

Each recipe lives at:

```text
packages/<package_id>.yaml
```

Rules:

- `schema_version` must be `1`.
- Package ids are unique within a repository.
- Component ids are unique within a recipe.
- Component `kind` must be one of `bootstrap`, `input`, `output`, `tool`,
  `skill`, `lib`, `core`, `mcp`, or `schedule`.
- Component sources must stay inside the repository.
- Component sources cannot be symlinks.
- Removed v1 fields such as `slots`, `tools`, `files`, `config_defaults`, and
  `metadata` are rejected.
- `manual_dependencies` are warnings only.
- Recipes do not install Python dependencies or edit host dependency files.

## Pipeline Rules

Pipeline edits are valid only for `bootstrap`, `input`, and `output`
components.

Rules:

- `bootstrap` supports only `group: serial`.
- `input` and `output` support `group: serial` and `group: parallel`.
- A pipeline entry must declare exactly one of `append`, `before`, or `after`.
- Install fails if a `before` or `after` target is missing.
- Install fails if the target pipeline already contains the package slot id.
- Uninstall removes package-owned pipeline entries.

## Component Targets

Directory components install into runtime-core-relative targets:

| Kind | Default target root |
| --- | --- |
| `bootstrap` | `agent/bootstrap/` |
| `input` | `agent/input/` |
| `output` | `agent/output/` |
| `tool` | `agent/tools/` |
| `skill` | `agent/skills/` |
| `lib` | `agent/lib/` |

`core` components create or update a package-owned runtime core named by
`target_core_id`.

Install fails when an unmanaged target already exists. Shared targets are
allowed only when another installed package owns the same repository alias,
source, target, and effective config hash.

## Manifest File Components

`mcp` and `schedule` components install one YAML declaration file each.

Defaults:

- `mcp` uses the target core's `slots.mcp` root, or `agent/mcp` when unset.
- `schedule` uses the target core's `slots.schedules` root, or
  `agent/schedules` when unset.

Rules:

- Source files live under `mcp/` or `schedule/`.
- Targets must be YAML files.
- Targets must be directly inside the declaration root.
- Component `config` is rendered with package options and applied as a manifest
  overlay before validation.
- Installed files are normalized with schema defaults.

MCP and schedule packages install declarations, not running servers or claimed
jobs. The host owns MCP transport, server lifecycle, schedule claims, approvals,
and execution.

Example:

```yaml
schema_version: 1
id: docs_and_daily
components:
  - id: docs
    kind: mcp
    source: docs.yaml
    config:
      url: ${options.url}
  - id: daily
    kind: schedule
    source: daily.yaml
    config:
      schedule: "0 9 * * *"
      prompt: "Write a daily summary."
```

## Options and Secrets

Supported option types are `string`, `bool`, `choice`, `path`, and `secret`.

Use `type: secret` for secret values. Secret values may be written into
installed component config, but `packages.yaml` records only `<redacted>`.

Unknown script-supplied option ids are rejected. Required options must resolve
to a non-empty value.

## Trust Rule

External repositories must be trusted before installation:

```bash
uv run demiurge package repo add ./local-packages --alias local --trust
```

The interactive manager asks for trust confirmation. Non-interactive external
adds require `--trust`.

Trust is host-local. A package cannot make itself trusted.

Path repositories are read from their configured path. Git repositories sync
into:

```text
~/.demiurge/package-repositories/<alias>/
```

## Install and Uninstall Contract

Install writes package-owned runtime core targets, pipeline entries for slot
components, and a package record in:

```text
~/.demiurge/agents/<core-id>/packages.yaml
```

Uninstall removes package-owned targets and pipeline entries unless another
installed package still references the same shared component. It then updates
`packages.yaml`.

Data outside package-owned targets is outside the uninstall contract.

## Verification

Validate a repository:

```bash
uv run demiurge package repo list
uv run demiurge package list --repo <alias>
```

Preview a package:

```bash
uv run demiurge package install <alias>/<package_id> --core assistant --preview
```

Check the runtime after installation:

```bash
uv run demiurge init --check
```
