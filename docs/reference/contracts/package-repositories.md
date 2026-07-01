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
- Component ids are unique within a recipe.
- Component sources stay inside the repository.
- Component sources cannot be symlinks.
- Pipeline edits are allowed only for bootstrap, input, and output components.
- Bootstrap pipeline edits are serial-only.
- `manual_dependencies` are warnings only.
- Recipes do not edit host dependency files.

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
