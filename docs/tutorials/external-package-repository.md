---
title: Create an External Package Repository
description: Build a small trusted package repository and install one input component into a runtime core.
---

# Create an External Package Repository

This tutorial creates a local package repository outside the Demiurge source
checkout. You will add one input slot package, trust the repository, preview the
install, and uninstall it again.

Package repositories distribute authored-surface files. They do not install
Python dependencies and do not modify the host `uv.lock`.

## 1. Create the Repository Root

Choose a local path:

```bash
mkdir -p ~/demiurge-packages/packages
mkdir -p ~/demiurge-packages/input/reply_style
```

Create `~/demiurge-packages/repository.yaml`:

```yaml
schema_version: 1
id: local_examples
name: Local Demiurge Examples
summary: Local example packages for testing.
```

`repository.yaml` identifies the repository. The local alias can still be
different when you add it to the host.

## 2. Add an Input Slot

Create `~/demiurge-packages/input/reply_style/module.py`:

```python
def process(ctx):
    ctx.input.add_context(
        "Package hint: answer with direct, concrete steps.",
        role="system",
        write_history=False,
    )
```

Create `~/demiurge-packages/input/reply_style/slot.yaml`:

```yaml
entrypoint: module:process
failure_policy: soft
history_policy: transient
capabilities: []
description: Adds a package-provided reply style hint.
```

Input slots run before the provider call. This example adds a low-priority
system context hint for each turn.

## 3. Add a Package Recipe

Create `~/demiurge-packages/packages/reply_style.yaml`:

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
`target` value is relative to the runtime core. Because this is an input slot,
the recipe must include a pipeline placement.

## 4. Add and Trust the Repository

```bash
uv run demiurge package repo add ~/demiurge-packages --alias local --trust
uv run demiurge package repo list
```

Trust is required because repositories can install executable local code into
host-shared Agent Core slots.

You can also use the interactive manager:

```bash
uv run demiurge package
```

Open **Repos**, add the path, review the detected repository metadata, and
confirm trust.

## 5. Preview and Install

List packages from the new repository:

```bash
uv run demiurge package list --repo local
```

Preview the install:

```bash
uv run demiurge package install local/reply_style --core assistant --preview
```

Install:

```bash
uv run demiurge package install local/reply_style --core assistant
```

The install writes into the active runtime core:

```text
~/.demiurge/agents/assistant/
```

It copies the input slot to:

```text
~/.demiurge/agents/assistant/agent/input/reply_style/
```

It also appends `reply_style` to the input pipeline and records the package in:

```text
~/.demiurge/agents/assistant/packages.yaml
```

## 6. Verify

Check installed package state:

```bash
uv run demiurge package list --core assistant
```

Check that the runtime core still loads:

```bash
uv run demiurge init --check
```

Run a fake-provider turn:

```bash
uv run demiurge --provider fake
```

If the package fails to load, compare the repository with
[Package Repository Contract](../reference/contracts/package-repositories.md)
and the recipe with
[Package Recipe Reference](../reference/package-recipes.md).

## 7. Uninstall

Preview removal:

```bash
uv run demiurge package uninstall local/reply_style --core assistant --preview
```

Uninstall:

```bash
uv run demiurge package uninstall local/reply_style --core assistant
```

Uninstall removes `agent/input/reply_style/`, removes the package-owned pipeline
entry, and updates `packages.yaml`. It does not delete files that a package
created outside package-owned targets.
