---
title: Package Installer
description: Contributor notes for package repository loading, previews, installs, and uninstalls.
---

# Package Installer

The package installer manages user-controlled runtime-core edits from package
repositories.

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
applies pipeline edits, installs child cores when requested, and records state
in the target core's `packages.yaml`.

## Uninstall

Uninstall removes package-owned component targets and updates `packages.yaml`.
It should not delete package data written outside component-owned targets.

## Boundary

The installer does not install dependencies or edit `uv.lock`. Use
`manual_dependencies` for dependency review warnings.
