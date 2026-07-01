---
title: Evolution Model
description: Understand candidate Agent Core evolution, promotion, and rollback.
---

# Evolution Model

Demiurge treats an Agent Core as a versionable filesystem surface.

Evolution is not arbitrary self-modification of the host runtime. It is a
host-owned workflow that edits a candidate copy of a core and promotes it only
after checks pass.

Agent Slots are a primary evolution surface. A candidate core can replace,
reorder, or compose slot behavior while the host keeps provider calls, tools,
approvals, state, promotion, and rollback behind stable contracts.

## Current Flow

1. The active core asks for evolution through the host tool runtime.
2. The host creates a candidate copy of the active core.
3. The host runs the `evolver` core with candidate-scoped editing tools.
4. The host checks that the candidate manifest still loads.
5. The host promotes the candidate if it changed files and passed the check.
6. The host records the version pointer.

Rollback is also host-owned.

## Evolver Scope

The `evolver` core may edit authored files inside the candidate workspace,
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
- registry files
- sessions
- production state
- release files
- dependency files
- runtime files outside the candidate
- `.temp/` reference checkouts

It also must not promote, roll back, install dependencies, or bypass host
capabilities.

## Contract

The exact agent-readable rules live in
[/docs/reference/contracts/evolver-safe-edits](/docs/reference/contracts/evolver-safe-edits).
