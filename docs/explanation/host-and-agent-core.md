---
title: Host and Agent Core
description: Understand the stable runtime boundary between the Demiurge host and authored Agent Cores.
---

# Host and Agent Core

Demiurge separates the runtime harness from the authored agent surface.

The **host** is stable infrastructure. It owns sessions, turns, provider calls,
tool execution, approvals, state, delivery, schedules, package installation,
background jobs, promotion, and rollback.

An **Agent Core** is the authored filesystem surface. It owns identity,
instructions, slot modules, skills, tools, schedules, MCP declarations, tests,
and local libraries.

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
- in-memory background job runtime
- approval and capability checks
- workspace enforcement
- external channel bridges
- scheduler claims and run logs
- package preview, install, and uninstall
- version promotion and rollback

## Agent-Core Responsibilities

The core owns:

- `agent.yaml`
- `agent/SOUL.md`
- bootstrap, input, and output slots
- authored tools
- skills
- schedules
- MCP server declarations
- local libraries
- core-local tests
- evolution policy expressed as authored files

## Important Consequence

Agent Core files may describe desired behavior, but they do not own provider
calls, direct state mutation, dependency installation, production promotion, or
rollback. Those remain host functions.

Background `evolve_core` work follows the same boundary: it may create a
candidate and report, but it does not switch the active core unless a later
foreground turn asks the host to do so.

For exact edit rules, read
[/docs/reference/contracts/authored-surface](/docs/reference/contracts/authored-surface).
