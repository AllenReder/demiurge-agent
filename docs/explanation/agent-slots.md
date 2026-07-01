---
title: Agent Slots
description: Understand Agent Slots as evolvable interaction boundaries in an Agent Core.
---

# Agent Slots

An **Agent Slot** is an evolvable interaction boundary in an Agent Core: it lets
Core-defined behavior enter the agent loop at governed points and compose tools,
skills, MCP, state, or other agents without changing the host harness.

The defining property of a slot is not the capability it provides. It is the
place where a capability can affect the agent loop under host governance. The
host still owns scheduling, provider calls, tool dispatch, approvals, delivery,
state enforcement, promotion, and rollback.

Slots are separate from the things they compose:

- A **tool** is a model-callable action.
- A **skill** is reusable knowledge, workflow, or policy.
- **MCP** is a protocol for external tools and context.
- An **agent** is another runnable loop.
- A **package** is a distribution unit that can install slots, tools, skills,
  libraries, and child cores together.

Current Demiurge slots are bootstrap, input, and output slots. They let an Agent
Core add session context, shape current-turn input, and handle final output.
Future slot kinds should represent new governed interaction boundaries, not
ordinary feature categories.

This makes slots a natural evolution surface. A candidate Agent Core can replace,
reorder, or compose slot behavior as files, while the host keeps risky effects
behind stable contracts.
