---
title: Evolve and Roll Back a Core
description: Use the host-owned evolution path and rollback controls.
---

# Evolve and Roll Back a Core

Demiurge can ask the host-managed `evolver` core to edit a candidate copy of an
active core. Promotion remains host-owned.

## Evolve from the TUI

Inside the TUI:

```text
/evolve Add a concise Telegram reply style input module.
```

The host creates a candidate core, runs the `evolver` core with
candidate-scoped tools, checks that the manifest loads, and promotes the
candidate only if the check passes.

## Give Functional Goals

Good evolution goals describe behavior and scope:

```text
Add an output module that emits a local Markdown artifact for long answers.
Change only agent/output and the output pipeline.
```

Avoid goals that ask the evolver to edit host runtime code, dependencies,
release files, source checkout files, or `.temp/`.

## Inspect Versions

Inside the TUI:

```text
/versions
```

## Roll Back

Inside the TUI:

```text
/rollback
```

Rollback switches back to a previous stable core version through the host
version store.

## Contract

For exact rules, read
[../reference/contracts/evolver-safe-edits.md](../reference/contracts/evolver-safe-edits.md).

The evolver may edit the authored surface of a candidate core. It must not
promote, roll back, edit host state, change dependencies, or edit files outside
the candidate workspace.
