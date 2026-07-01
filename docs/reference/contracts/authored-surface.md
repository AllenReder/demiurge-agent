---
title: Authored Surface Contract
description: Stable rules for files owned by an Agent Core.
---

# Authored Surface Contract

This page defines the authored surface of a Demiurge Agent Core. It is intended
for human authors and for the `evolver` core when project docs are supplied as
read-only reference context.

## Core Root

A concrete runtime core has this shape:

```text
<core>/
  agent.yaml
  packages.yaml
  agent/
    SOUL.md
    bootstrap/
    input/
    output/
    tools/
    skills/
    schedules/
    mcp/
    lib/
    tests/
```

`packages.yaml` is package install state. Do not edit it manually unless you are
repairing package state with explicit user direction.

## Owned by the Agent Core

Agent Core authors may edit:

- `agent.yaml`
- `agent/SOUL.md`
- `agent/bootstrap/`
- `agent/input/`
- `agent/output/`
- `agent/tools/`
- `agent/skills/`
- `agent/schedules/`
- `agent/mcp/`
- `agent/lib/`
- `agent/tests/`

## Owned by the Host

Agent Core authors must not take ownership of:

- provider request construction
- provider calls
- session, turn, and step storage
- tool registry and dispatch
- approval decisions
- workspace enforcement
- production state mutation
- package repository trust
- dependency installation
- promotion or rollback

## Dependency Rule

Current runtime mode is `host_shared`. Agent Core code slots run in the host
Python environment. Candidate cores must not add Python dependencies
automatically. If a change needs a dependency, document it as a manual dependency
review item.

## Verification

After authored-surface edits:

```bash
uv run demiurge init --check
uv run demiurge --provider fake
```

Use narrower checks from the relevant page when editing packages, schedules, MCP
servers, or tools.
