---
title: Host and Agent Core
description: Understand the stable runtime boundary between the Demiurge host and authored Agent Cores.
---

# Host and Agent Core

Demiurge separates the runtime harness from the authored agent surface.

The **host** is stable infrastructure. It owns sessions, turns, provider calls,
tool execution, approvals, state, delivery, schedules, package installation,
background runtime tasks, Git revision promotion, and rollback.

An **Agent Core** is the authored filesystem surface. It owns identity,
instructions, Agent Slots, skills, tools, schedules, MCP declarations, and
local libraries.

An **Agent Slot** is the core's evolvable interaction boundary. It lets
Core-defined behavior enter the agent loop at a governed point and compose
tools, skills, MCP, state, or other agents without changing the host harness.

## Why This Boundary Exists

Self-evolving agents need room to change behavior without making the runtime
loop arbitrary and self-modifying. Demiurge allows Agent Cores to evolve files,
but keeps risky effects behind host-controlled capabilities.

This gives three useful properties:

- Agent behavior is readable as files.
- Candidate changes are diffable and gateable.
- Dangerous effects remain controlled by the host.

## Host-Owned Responsibilities

The host owns:

- runtime home resolution
- source template initialization
- core loading and validation
- session, turn, and step storage
- context assembly
- provider request construction
- provider calls
- tool registry and dispatch
- runtime task control and active task workers
- approval and capability checks
- workspace enforcement
- external channel bridges
- scheduler claims and run logs
- package preview, install, and uninstall
- Git-backed Agent Core revisions
- revision promotion and rollback

## Agent-Core Responsibilities

The core owns:

- `agent.yaml`
- `agent/SOUL.md`
- Agent Slots, currently bootstrap, input, and output slots
- authored tools
- skills
- schedules
- MCP server declarations
- local libraries
- evolution policy expressed as authored files

## Important Consequence

Agent Core files may describe desired behavior, but they do not own provider
calls, direct state mutation, dependency installation, live revision promotion,
or rollback. Those remain host functions.

`evolve_core` follows the same boundary: `start` creates an isolated agents-tree
worktree, `review` records a proposal revision, and `promote` or `rollback`
advance host-owned Git refs only through approved host operations.

For exact edit rules, read
[/docs/reference/contracts/authored-surface](/docs/reference/contracts/authored-surface).
