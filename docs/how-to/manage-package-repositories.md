---
title: Manage Package Repositories
description: Add, list, sync, and remove built-in, local path, and git package repositories.
---

# Manage Package Repositories

Package repositories are host-level sources for package recipes. The built-in
repository is available by default. Add external repositories only when you
trust their code.

The easiest path is the interactive package manager:

```bash
uv run demiurge package
```

Open **Repos** to list repositories, add a path or git source, sync git caches,
or remove a configured source.

Use the subcommands below when you need an explicit command.

## List Repositories

```bash
uv run demiurge package repo list
```

For machine-readable output:

```bash
uv run demiurge package repo list --json
```

The list shows each repository alias, source type, package count, root or git
ref, commit when known, and readiness status.

## Add a Local Path Repository

A path repository points at an existing local directory:

```bash
uv run demiurge package repo add ~/demiurge-packages \
  --alias local \
  --trust
```

Use `--subdir` when the package repository lives below the path:

```bash
uv run demiurge package repo add ~/workspace/community \
  --alias community \
  --subdir demiurge-packages \
  --trust
```

`--trust` is required for non-interactive external adds. Without it, the
interactive manager asks for confirmation.

## Add a Git Repository

```bash
uv run demiurge package repo add https://github.com/user/demiurge-packages.git \
  --alias community \
  --ref main \
  --trust
```

Git repositories are synced into:

```text
~/.demiurge/package-repositories/<alias>/
```

Use `--ref` for a branch, tag, or commit. Use `--subdir` when the git checkout
contains the package repository below the root.

## Sync Repositories

Sync all configured repositories:

```bash
uv run demiurge package repo sync
```

Sync one repository:

```bash
uv run demiurge package repo sync community
```

For git repositories, sync fetches remote updates and checks out the configured
ref. For path repositories, sync validates the current directory.

## Install from a Repository

After adding a repository, list its packages:

```bash
uv run demiurge package list --repo community
```

Install with a repository-qualified package ref:

```bash
uv run demiurge package install community/reply_style --core assistant --preview
uv run demiurge package install community/reply_style --core assistant
```

Repository-qualified refs avoid ambiguity when different repositories contain
the same package id.

## Remove a Repository

```bash
uv run demiurge package repo remove community
```

The built-in repository cannot be removed.

If installed package records still reference the repository, removal is blocked.
Uninstall those packages first. Use `--force` only when you intentionally want
to remove the repository source while leaving installed package records in
runtime cores:

```bash
uv run demiurge package repo remove community --force
```

Removing a git repository also removes its cache under
`~/.demiurge/package-repositories/<alias>/`. Removing a path repository removes
only the host configuration entry.

## Trust Boundary

External repositories can install executable Python slot code, authored tools,
skills, libraries, child cores, MCP declarations, and schedule declarations into
runtime Agent Cores.

Trust is local host policy. A repository or package cannot make itself trusted.
Review `repository.yaml`, package recipes under `packages/`, and component
source files before adding an external source.

Packages still do not install Python dependencies or edit `uv.lock`. If a
recipe declares `manual_dependencies`, treat those strings as warnings that need
manual review.
