---
title: Package Repository Contract
description: Stable rules for external package repositories and package recipes.
---

# Package Repository Contract

Package repositories install reusable authored-surface files into runtime Agent
Cores. They must be safe to inspect, preview, and uninstall.

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

## Recipe Rules

Each recipe lives at:

```text
packages/<package_id>.yaml
```

Rules:

- Package ids are unique within the repository.
- Entry ids are unique within a recipe.
- Entry sources stay inside the repository.
- Entry sources cannot be symlinks.
- Pipeline edits are allowed only for entries in `slots`.
- Bootstrap pipeline edits are serial-only.
- `mcp` and `schedule` file entries install one YAML file each.
- `mcp` defaults to the target core's `slots.mcp` root.
- `schedule` defaults to the target core's `slots.schedules` root.
- `mcp` and `schedule` targets must be YAML files directly inside their slot
  root.
- `mcp` and `schedule` entry `config` is rendered with package options and
  applied as a manifest overlay before validation.
- `manual_dependencies` are warnings only.
- Recipes do not edit host dependency files.

## Manifest File Components

MCP and schedule recipes install declarations, not runtime jobs or running
servers. The host still owns MCP transport, schedule claims, approvals, and
execution.

```yaml
schema_version: 3
id: docs_and_daily
files:
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
slots: []
tools: []
config_defaults: {}
capabilities: []
```

The source files may be incomplete bases as long as the rendered final manifest
is valid. Installed files are normalized with explicit schema defaults.

## Trust Rule

External repositories must be trusted before installing local executable code:

```bash
uv run demiurge package repo add ./local-packages --alias local --trust
```

Trust is host-local. A package cannot make itself trusted.

## Secret Rule

Use `type: secret` for secret options. Secret values may be written to installed
component config, but `packages.yaml` records only `<redacted>`.

## Verification

```bash
uv run demiurge package repo list
uv run demiurge package list --repo <alias>
uv run demiurge package install <alias>/<package_id> --core assistant --preview
uv run demiurge init --check
```
