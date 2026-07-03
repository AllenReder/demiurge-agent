---
title: Evolve and Roll Back a Core
description: Use the host-owned evolution path and rollback controls.
---

# Evolve and Roll Back a Core

Demiurge can ask the host-managed `evolver` core to edit an isolated Git
worktree of the runtime agents tree. Review creates a proposal commit; promote
advances the live Git ref.

## Evolve from the TUI

Inside the TUI:

```text
/evolve Add a concise Telegram reply style input module.
```

The host creates `.evolve/runs/<run_id>/agents`, runs the `evolver` core with
worktree-scoped tools, and reports a `run_id`. If `~/.demiurge/agents` has
local agent edits, Demiurge validates and saves them first, then creates the
evolve worktree from that new live revision. The live core is otherwise
unchanged by start.

Review the run:

```text
/evolve review <run_id>
```

Review runs host-owned gates and creates or updates
`refs/demiurge/runs/<run_id>`.

Promote the reviewed run:

```text
/evolve promote <run_id>
```

Promote reruns gates, advances `refs/demiurge/previous` and
`refs/demiurge/live`, and takes effect on the next turn.

Promote does not auto-save local agent edits. If the live agents tree is dirty,
save those edits with `uv run demiurge core save` or discard them with
`uv run demiurge core discard --yes`, then promote again.

Discard an unwanted run:

```text
/evolve discard <run_id>
```

## Give Functional Goals

Good evolution goals describe behavior and scope:

```text
Add an output module that emits a local Markdown artifact for long answers.
Change only agent/output and the output pipeline.
```

Avoid goals that ask the evolver to edit host runtime code, dependencies,
release files, source checkout files, or `.temp/`.

## Inspect Revisions

Inside the TUI:

```text
/versions
```

## Roll Back

Inside the TUI:

```text
/rollback
```

Rollback returns the agents tree to a previous Git revision by creating a new
rollback commit. It takes effect on the next turn.

Rollback also refuses to run while local agent edits remain. Use
`uv run demiurge core diff` to inspect them, then save or discard before
rolling back.

Use a specific target when needed:

```text
/rollback <revision>
```

## Contract

For exact rules, read
[/docs/reference/contracts/evolver-safe-edits](/docs/reference/contracts/evolver-safe-edits).

The evolver may edit the authored surface inside the isolated agents-tree
worktree. It must not promote, roll back, edit host state, change dependencies,
or edit files outside that worktree.
