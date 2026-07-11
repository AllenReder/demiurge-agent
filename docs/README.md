---
slug: /
sidebar_position: 0
title: Demiurge Manual
description: User manual for installing Demiurge, configuring providers, choosing workspaces, and authoring self-evolving Agent Cores with Agent Slots.
---

# Demiurge Manual

Demiurge is an Alpha-stage agent framework built around **Agent Slots**:
governed extension boundaries that let an Agent Core expand capability and
logic design without changing the Host harness. A file-backed Agent Core can
compose agents, state, tools, skills, and MCP declarations, then evolve through
Host-controlled Git change sets.

The Host owns sessions, turns, provider calls, tools, approvals, state,
delivery, Git revision promotion, and rollback. Agent Cores own authored files such as
`agent.yaml`, `SOUL.md`, Agent Slots, skills, tools, schedules, MCP
declarations, and local libraries.

Start with [Agent Slots](explanation/agent-slots.md) if you want to understand
how custom behavior enters the agent loop under Host governance.

The manual uses the Diataxis documentation model:

- **Tutorials** guide you through a complete learning path.
- **How-to guides** solve one operational task.
- **Explanation** pages describe why the system is shaped this way.
- **Reference** pages define exact commands, schemas, and contracts.

Reference contract pages are also intended to be readable by the `evolver` core
when it receives project docs as read-only context.

## Start Here

If you are new to Demiurge, read these in order:

1. [Quick Start](tutorials/quick-start.md)
2. [Configure a provider](how-to/configure-provider.md)
3. [Choose a workspace](how-to/choose-workspace.md)
4. [Troubleshoot](how-to/troubleshoot.md)

## By Role

| Role | First pages |
| --- | --- |
| First-time user | [Quick Start](tutorials/quick-start.md), [Configure a provider](how-to/configure-provider.md), [Choose a workspace](how-to/choose-workspace.md) |
| Local operator | [Troubleshoot](how-to/troubleshoot.md), [Configure channels](how-to/configure-channels.md), [Install packages](how-to/install-packages.md) |
| Agent Core author | [Host and Agent Core](explanation/host-and-agent-core.md), [Customize an Agent Core](tutorials/customize-agent-core.md), [Write an Agent Slot](how-to/write-slot-module.md), [Slot Context SDK](reference/slot-context-sdk.md), [Authored surface contract](reference/contracts/authored-surface.md) |
| Package author | [Package model](explanation/package-model.md), [Write a package recipe](how-to/write-package-recipe.md), [Create an external package repository](tutorials/external-package-repository.md), [Publish a package repository](how-to/publish-package-repository.md), [Package recipe reference](reference/package-recipes.md) |
| Contributor | [Architecture](developer-guide/architecture.md), [Host runtime contracts](developer-guide/runtime-contracts.md), [Runner and context](developer-guide/runner-and-context.md), [Tool runtime](developer-guide/tool-runtime.md), [Package installer](developer-guide/package-installer.md) |

## Install Paths

For normal use, run the managed installer from a checkout of this repository:

```bash
scripts/install.sh
~/.demiurge/demiurge-agent/.venv/bin/demiurge --provider fake
```

The installer requires `git` and `uv`, creates or reuses
`~/.demiurge/demiurge-agent`, runs `uv sync`, and initializes the runtime home.

For source checkout development, stay in the repository and use `uv`:

```bash
uv sync --all-groups
uv run demiurge init
uv run demiurge --provider fake
```

The TUI requires Node.js 20 or newer. Running `demiurge` without a subcommand
starts the TUI. The main subcommands are `init`, `doctor`, `core`, `package`,
`update`, `setup`, and `gateway`.

The launcher uses the tracked packaged TUI bundle by default and ignores a
leftover source-checkout `ui-tui/dist/entry.js`. Use `DEMIURGE_TUI_DEV=1` only
when intentionally running local TUI build/source artifacts. The TUI and Host
exchange protocol/build identity before gateway initialization and fail closed
on a mismatch.

## Configuration Order

Provider resolution uses this order:

1. CLI override such as `--provider <provider-id>`.
2. The selected runtime core manifest.
3. The global fallback manifest.
4. The host default provider.
5. `fake`.

Workspace resolution uses this order:

1. `--workspace <path>`.
2. `DEMIURGE_WORKSPACE`.
3. The TUI launch directory.
4. The selected core's `runtime.workspace`.
5. `~/.demiurge/workspace`.

## Current Alpha Boundaries

- Latest release notes: [0.8.0](releases/0.8.0.md).
- Python dependencies are host-owned and locked by the source checkout.
- Agent Slot code runs in the host-shared Python environment.
- Runtime Agent Core revisions are Git commits in `~/.demiurge/.core.git`.
- Candidate evolution works in `.evolve/runs/<run_id>/agents` and cannot add
  dependencies automatically.
- Package install and uninstall are user-triggered Git transactions against the
  live agents tree; package recipes do not modify the host lock file.
- Runtime layout, authoring contracts, package behavior, and troubleshooting
  steps may still change before `1.0.0`.
