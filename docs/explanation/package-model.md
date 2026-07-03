---
title: Package Model
description: Understand package repositories, recipes, components, trust, install state, and host boundaries.
---

# Package Model

Demiurge packages are recipes for installing reusable authored-surface files
into runtime Agent Cores.

They are not Python packages. They do not install dependencies. They do not edit
`uv.lock`. If a package needs something outside the locked host environment, it
can declare `manual_dependencies`, which become human-review warnings.

## Why Packages Exist

An Agent Core owns authored behavior: slots, tools, skills, libraries, child
cores, MCP declarations, and schedule declarations. A package lets that
authored behavior be reused without editing the source template by hand.

The host still owns the runtime harness: sessions, turns, provider calls,
approvals, capabilities, MCP transport, schedule execution, state, Git
revisions, promotion, and rollback.

Package management is a user-controlled CLI workflow. It is not exposed as a
model-callable tool.

## Repository

A package repository is a local directory or git checkout with a repository
manifest and package recipes:

```text
repository.yaml
packages/
```

It can also contain component roots:

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

The built-in repository lives in the source tree at `package-repository/` and is
loaded as the `builtin` repository. External path and git repositories are added
to the host with `demiurge package repo add`.

Git repositories sync into:

```text
~/.demiurge/package-repositories/<alias>/
```

## Recipe

A recipe lives under:

```text
packages/<package_id>.yaml
```

It describes package identity, options, capability summary, manual dependency
warnings, and components. Install-time options can select components, patch
component config, and be redacted when they are secret.

The current package runtime is implemented by `demiurge/packages.py`; built-in
package recipes live under `package-repository/packages/`.

## Components

Supported component kinds are:

| Kind | What it installs |
| --- | --- |
| `bootstrap` | A bootstrap slot under the target core's `agent/bootstrap/`. |
| `input` | An input slot under `agent/input/`. |
| `output` | An output slot under `agent/output/`. |
| `tool` | An authored tool under `agent/tools/`. |
| `skill` | A skill under `agent/skills/`. |
| `lib` | Package-owned helper code or config under `agent/lib/`. |
| `core` | A package-owned runtime child core. |
| `mcp` | One MCP server declaration YAML file. |
| `schedule` | One schedule declaration YAML file. |

Only `bootstrap`, `input`, and `output` components edit
`agent/pipelines.yaml`. Bootstrap pipeline entries are serial-only. Input and
output entries can be serial or parallel.

`mcp` and `schedule` components install declarations, not running services. The
host still owns MCP transport, server lifecycle, schedule claims, approvals, and
schedule execution.

## Install State

Installing a package writes files into the live runtime agents tree under the
core repository lock, runs host-owned gates, commits the result, and records
provenance in:

```text
~/.demiurge/agents/<core-id>/packages.yaml
```

The install record stores the package id, repository alias, repository metadata,
tags, installed component targets, installed file/tree hashes, warnings, and
redacted options. It does not store full effective config or secrets, and it is
not runtime truth.

Installation rejects target conflicts unless the target is already owned by an
installed package with the same repository alias, source, target, and effective
config hash. This lets packages share identical helper components without
silently overwriting local files.

## Uninstall State

Uninstall removes package-owned targets and removes package-owned pipeline
entries for `bootstrap`, `input`, and `output` slots. If another installed
package still references the same shared component, the target is kept.

Uninstall refuses drifted package-owned targets unless the operator supplies an
explicit destructive strategy such as `--force-drift`. It then updates
`packages.yaml` and commits the runtime agents tree. If no packages remain, the
provenance file can be removed.

Uninstall does not remove data written outside package-owned targets. Examples
include memory files, generated audio, context reseed notes, provider caches,
and outbox files.

## Trust

The built-in repository is trusted. External repositories are not trusted until
the host user confirms trust.

Trust matters because packages can install executable Python code into
host-shared Agent Core slots, tools, skills, and libraries. A package cannot
grant trust to itself. Trust is a host-local decision recorded in the host
package repository configuration.

## Repository Lifecycle

Use the interactive manager for normal repository work:

```bash
uv run demiurge package
```

The scriptable subcommands are:

```bash
uv run demiurge package repo list
uv run demiurge package repo add <path-or-git-url> --alias <alias> --trust
uv run demiurge package repo sync [alias]
uv run demiurge package repo remove <alias>
```

Removing a repository source does not uninstall packages already copied into
runtime cores. Without `--force`, removal is blocked when installed package
records still reference that repository.
