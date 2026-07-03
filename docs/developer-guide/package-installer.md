---
title: Package Installer
description: Contributor notes for package repository loading, previews, installs, and uninstalls.
---

# Package Installer

The package installer plans user-controlled runtime-core edits from package
repositories. Actual install and uninstall operations run as host-owned Git
transactions against the live agents tree.

## Load

The loader reads:

```text
repository.yaml
packages/*.yaml
```

It validates repository identity, package ids, options, components, conditions,
and component source paths.

## Preview

Preview resolves:

- package reference
- selected options
- included components
- config writes
- pipeline edits
- warnings
- target paths

Preview must not write runtime files.

## Install

Install copies component files, writes component `config.yaml` when needed,
applies pipeline edits, installs child cores when requested, records provenance
hashes in the target core's `packages.yaml`, runs host-owned gates, and commits
the live agents tree.

## Uninstall

Uninstall removes package-owned component targets and updates `packages.yaml`.
It refuses drifted files unless the caller supplies an explicit destructive
strategy such as `--force-drift`. It should not delete package data written
outside component-owned targets.

## Boundary

The installer does not install dependencies or edit `uv.lock`. Use
`manual_dependencies` for dependency review warnings. `packages.yaml` is
provenance, not runtime truth; runtime loading comes from the committed files in
the agents tree.
