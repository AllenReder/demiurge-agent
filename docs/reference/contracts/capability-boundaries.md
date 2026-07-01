---
title: Capability Boundary Contract
description: Stable rules for effects, approvals, workspace scope, and host-owned controls.
---

# Capability Boundary Contract

Demiurge capabilities are host-owned. Authored files may request effects; the
host decides whether they run.

## Host-Owned Effects

These effects must go through host-owned interfaces:

- filesystem reads and writes
- terminal execution
- network fetches
- provider calls
- tool execution
- schedule management
- production state mutation
- version promotion
- rollback
- package repository trust
- dependency changes

## Workspace Rule

File and terminal operations must stay inside the resolved workspace unless the
host explicitly allows otherwise. A slot should not hard-code private local
paths.

## Approval Rule

Approval policy can come from built-in tool metadata, `agent.yaml` overrides,
risk policy, capability policy, or channel/runtime policy. The more restrictive
policy wins.

## Secrets Rule

Slots and tools should not print secrets. Provider secrets belong in host config,
environment variables, or `.env`. Status output should show sources, not values.

## Channel Rule

External channels must validate inbound requests before creating a turn.
Telegram allowlists, webhook tokens, Slack signatures, and similar checks are
channel bridge responsibilities.

## Dependency Rule

Candidate Agent Cores and package recipes must not install Python dependencies.
Record dependency needs as manual review items.
