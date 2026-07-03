---
title: Evolution Model
description: Understand candidate Agent Core evolution, promotion, and rollback.
---

# Evolution Model

Demiurge treats the runtime agents tree as a Git-versioned filesystem surface.

Evolution is not arbitrary self-modification of the host runtime. It is a
host-owned workflow that edits an isolated worktree and promotes it only after
review gates pass.

Agent Slots are a primary evolution surface. A candidate core can replace,
reorder, or compose slot behavior while the host keeps provider calls, tools,
approvals, state, promotion, and rollback behind stable contracts.

## Current Flow

1. The active core asks for evolution through the host tool runtime.
2. The host creates `.evolve/runs/<run_id>/agents` as a Git worktree of
   `refs/demiurge/live`.
3. The host runs the `evolver` core with worktree-scoped editing tools.
4. Review runs host-owned gates and records the proposal at
   `refs/demiurge/runs/<run_id>`.
5. Promote reruns gates, advances `refs/demiurge/previous` and
   `refs/demiurge/live`, and refreshes the live agents checkout.

Rollback is also host-owned.

## Evolver Scope

The `evolver` core may edit authored files inside the candidate agents tree,
especially:

- `agent/skills/`
- `agent/tools/`
- `agent/input/`
- `agent/output/`
- `agent/bootstrap/`

It may change `agent.yaml` only when that is the minimum needed to keep the
candidate loadable after an authored-surface edit.

## What Evolution Must Not Do

Candidate evolution must not edit:

- source checkout files
- host config
- sessions
- production state
- release files
- dependency files
- runtime files outside the candidate
- `.core.git` refs directly
- `.temp/` reference checkouts

It also must not promote, roll back, install dependencies, or bypass host
capabilities.

## Contract

The exact agent-readable rules live in
[/docs/reference/contracts/evolver-safe-edits](/docs/reference/contracts/evolver-safe-edits).
