---
title: Publish a Package Repository
description: Prepare, verify, and share a local or git package repository for other Demiurge users.
---

# Publish a Package Repository

Use this guide when a package repository works locally and you want other
Demiurge users to add it as a path or git source.

Package repositories distribute authored-surface files. They are not Python
packages, do not install dependencies, and do not edit the host `uv.lock`.

If you have not created a repository yet, start with
[Create an External Package Repository](../tutorials/external-package-repository.md).
If you need to design `packages/<package_id>.yaml`, read
[Write a Package Recipe](write-package-recipe.md).
For exact schema rules, use the
[Package Repository Contract](../reference/contracts/package-repositories.md)
and [Package Recipe Reference](../reference/package-recipes.md).

## 1. Prepare the Repository Root

A distributable repository must contain:

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

Use a stable repository id in `repository.yaml`:

```yaml
schema_version: 1
id: community
name: Community Packages
summary: Shared Demiurge package recipes.
```

The repository id identifies the source. Users can still choose a different
local alias when they add the repository.

## 2. Keep Package Files Together

Each package recipe lives under:

```text
packages/<package_id>.yaml
```

Each component source lives under the matching component root. For example, an
input package can use:

```text
packages/reply_style.yaml
input/reply_style/
  slot.yaml
  module.py
```

The recipe connects the package id to the component source and target:

```yaml
schema_version: 1
id: reply_style
name: Reply Style
summary: Add a package-provided reply style hint.
tags:
  - input
  - style
components:
  - id: reply_style_input
    kind: input
    source: reply_style
    target: agent/input/reply_style
    pipeline:
      group: serial
      append: true
capabilities: []
```

The `source` value points to `input/reply_style/` inside the repository. The
`target` value is relative to the runtime core that installs the package.

## 3. Verify Locally

Add the repository as a trusted local path:

```bash
uv run demiurge package repo add ~/demiurge-packages \
  --alias local \
  --trust
```

Check that Demiurge can read it:

```bash
uv run demiurge package repo list
uv run demiurge package list --repo local
```

Preview the package before writing files into a runtime core:

```bash
uv run demiurge package install local/reply_style \
  --core assistant \
  --preview
```

Install only after the preview is correct:

```bash
uv run demiurge package install local/reply_style --core assistant
```

Check that the target runtime core still loads:

```bash
uv run demiurge init --check
```

For automated checks, the repository and package commands also support
machine-readable output:

```bash
uv run demiurge package repo list --json
uv run demiurge package list --repo local --json
uv run demiurge package install local/reply_style --core assistant --preview --json
```

## 4. Share the Repository

For a local team path, users can add the directory directly:

```bash
uv run demiurge package repo add /path/to/demiurge-packages \
  --alias team \
  --trust
```

For a git repository, publish the repository root and tell users which ref to
use:

```bash
uv run demiurge package repo add https://github.com/user/demiurge-packages.git \
  --alias community \
  --ref v0.1.0 \
  --trust
```

Use a tag or commit for a stable release. Use a branch such as `main` only when
users intentionally want a moving source.

If the package repository is inside a larger git repository, document the
subdirectory:

```bash
uv run demiurge package repo add https://github.com/user/community.git \
  --alias community \
  --ref v0.1.0 \
  --subdir demiurge-packages \
  --trust
```

Git repositories sync into:

```text
~/.demiurge/package-repositories/<alias>/
```

## 5. Publish Updates

Before publishing an update:

- Keep existing package ids stable unless users should treat the package as a
  different package.
- Keep component targets stable unless the release notes tell users how to
  migrate local runtime cores.
- Re-run the local repository list, package list, install preview, and
  `uv run demiurge init --check` checks.
- Publish a new git commit or tag.

Users refresh a configured git repository with:

```bash
uv run demiurge package repo sync community
```

Syncing updates the repository source used for future list and install
commands. It does not update files already installed into runtime cores.

To apply a changed package to an existing runtime core, users should preview the
change, uninstall the installed package, and install the package again:

```bash
uv run demiurge package uninstall community/reply_style --core assistant --preview
uv run demiurge package uninstall community/reply_style --core assistant
uv run demiurge package install community/reply_style --core assistant --preview
uv run demiurge package install community/reply_style --core assistant
```

Uninstall removes package-owned targets and package-owned pipeline entries. It
does not remove data the package wrote outside package-owned targets.

## Security and Dependency Boundaries

External repositories can install executable Python slot code, authored tools,
skills, libraries, child cores, MCP declarations, and schedule declarations into
runtime Agent Cores.

Trust is a local host decision. A package cannot make itself trusted.

Do not publish secrets in package recipes or component config. Use package
options with `type: secret`, environment variables documented by the component,
or local runtime configuration owned by the installing user.

Package recipes still cannot install Python dependencies or edit `uv.lock`.
Declare `manual_dependencies` only as warnings for human review.
