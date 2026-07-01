---
title: Create an External Package Repository
description: Build a small trusted package repository and install one input component into a runtime core.
---

# Create an External Package Repository

This tutorial creates a local package repository outside the Demiurge source
checkout. The repository installs one input module into the runtime `assistant`
core.

Package repositories are for reusable Agent Core components. They install files
into runtime cores; they do not modify source templates or install Python
dependencies.

Packages can combine Agent Slots with tools, skills, libraries, and child
cores. This tutorial installs one input slot.

## 1. Create the Repository

Choose a local path:

```bash
mkdir -p ~/demiurge-packages/packages
mkdir -p ~/demiurge-packages/input/reply_style
```

Add `repository.yaml`:

```yaml
schema_version: 1
id: local_examples
name: Local Demiurge Examples
summary: Local example packages for testing.
```

## 2. Add an Input Component

Create `input/reply_style/slot.yaml`:

```yaml
entrypoint: module:process
description: "Adds a package-provided reply style hint."
failure_policy: soft
capabilities: []
```

Create `input/reply_style/module.py`:

```python
def process(ctx):
    ctx.input.add("system", "Package hint: answer with direct, concrete steps.")
```

## 3. Add a Package Recipe

Create `packages/reply_style.yaml`:

```yaml
schema_version: 2
id: reply_style
name: Reply Style
summary: Add a package-provided reply style hint.
tags:
  - style
components:
  - id: reply_style_input
    kind: input
    source: reply_style
    target: agent/input/reply_style
    pipeline:
      before: base_input
```

The `source` path is repository-relative under `input/`. The `target` path is
runtime-core-relative.

## 4. Trust and Add the Repository

```bash
uv run demiurge package repo add ~/demiurge-packages --alias local --trust
uv run demiurge package repo list
```

Trust is explicit because repositories can install executable Python slot code
into runtime cores.

## 5. Preview and Install

```bash
uv run demiurge package list --repo local
uv run demiurge package install local/reply_style --core assistant --preview
uv run demiurge package install local/reply_style --core assistant
```

The install modifies:

```text
~/.demiurge/agents/assistant/
```

It records install state in:

```text
~/.demiurge/agents/assistant/packages.yaml
```

## 6. Verify

```bash
uv run demiurge init --check
uv run demiurge --provider fake
```

If the package fails to load, read the exact error and compare the recipe with
[../reference/contracts/package-repositories.md](../reference/contracts/package-repositories.md).

## 7. Uninstall

```bash
uv run demiurge package uninstall local/reply_style --core assistant --preview
uv run demiurge package uninstall local/reply_style --core assistant
```

Uninstall removes package-owned component targets and updates `packages.yaml`.
It does not delete data files that the component created outside its owned
targets.
