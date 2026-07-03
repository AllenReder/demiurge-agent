---
title: Runtime Home
description: Understand the local runtime directory layout under ~/.demiurge.
---

# Runtime Home

Demiurge is local-first. Runtime state lives under a runtime home, usually:

```text
~/.demiurge
```

The source checkout and runtime home have different roles.

## Main Layout

```text
~/.demiurge/
  config.yaml
  .env
  .core.git/
  .core.lock
  .evolve/
    runs/
  agents/
    agent.yaml
    assistant/
    evolver/
  runtime/
    runtime.sqlite3
    artifacts/
    session-events/
  workspace/
  logs/
```

`config.yaml` is host-owned runtime configuration. `.env` can hold local
provider secrets. `.core.git/` is the bare Git repository for the runtime
agents tree, and `agents/` is the live checkout of that tree. `.evolve/` holds
isolated change-set worktrees. `runtime/` contains the SQLite control-plane
database, delivery outbox projection, scheduler runtime projections, session
event logs, and host-owned artifacts. `workspace/` is the non-local fallback
workspace.

## Source Templates vs Runtime Cores

The repository contains source templates under:

```text
agents/
```

On a fresh runtime home, `demiurge init` commits that tree into:

```text
~/.demiurge/.core.git
```

and checks out the live agents tree at:

```text
~/.demiurge/agents/
```

Edit runtime cores for local behavior changes. Edit source templates only when
you are changing the default packaged project behavior. This release does not
migrate legacy runtime homes; delete an old `~/.demiurge` before first run if it
was created by an older layout.

## Local Agent Edits

You can edit files directly under `~/.demiurge/agents/`. Demiurge treats those
changes as local agent edits and saves them as Git-backed core revisions at
clear workflow boundaries.

Run/edit workflows, package install/uninstall, `setup model set`, and
`evolve start` validate and save local agent edits before they continue. The
save is a separate commit from the package, setup, or evolve transaction, so
the source of each revision remains visible.

Read-only commands such as `core status`, `core diff`, package previews, and
package lists do not save or discard anything. Revision-switching commands such
as `evolve promote` and rollback refuse to switch while local agent edits
remain, because switching would overwrite or strand those files.

Use these commands when you want to manage the edits yourself:

```bash
uv run demiurge core diff
uv run demiurge core save
uv run demiurge core discard --yes
```

## Managed Checkout

Managed install places the checkout at:

```text
~/.demiurge/demiurge-agent
```

Live runtime cores remain separate Git revisions, so updating the managed
checkout does not overwrite edited Agent Cores.

## Drift

Use read-only drift checks before refreshing runtime files:

```bash
uv run demiurge init --check
uv run demiurge doctor
```

Refresh intentionally. Refresh is a Git transaction that creates a new live
revision from the source templates:

```bash
uv run demiurge init --refresh assistant
```

Inspect the live repository:

```bash
uv run demiurge core status
uv run demiurge core versions
uv run demiurge core check
```
