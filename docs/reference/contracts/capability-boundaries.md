---
title: Capability Boundary Contract
description: Stable rules for effects, approvals, workspace scope, and host-owned controls.
---

# Capability Boundary Contract

Demiurge capabilities are host-owned. Authored files may request effects; the
host decides whether those effects run.

## Host-Mediated Effects

These effects must go through host-owned interfaces or explicit host capability
checks:

- filesystem reads and writes
- terminal execution
- network fetches
- provider calls
- tool execution
- MCP tool calls
- schedule management
- state reads and writes
- child agent runs and spawns
- Git revision promotion
- rollback
- package repository trust
- dependency changes

## Capability Rule

Authored code must require the capability it depends on:

```python
ctx.capability.require("fs.read", slot_path=ctx.slot_path)
```

The capability must be declared in one of these places:

- `agent.yaml` under `capabilities.defaults`
- `agent.yaml` under `capabilities.slots.<slot_path>`
- the component manifest's `capabilities` list

Prefix grants such as `mcp.call:*` may grant scoped capabilities such as
`mcp.call:docs`.

## Approval Rule

Approval policy can come from:

- built-in tool metadata
- authored tool metadata
- MCP server metadata
- `tools.metadata`
- `agent.yaml` approval config
- global fallback approval config
- channel/runtime approval provider behavior

`deny` is always terminal. `prompt` requires an approval provider. `auto` can
run without asking only after capability and workspace checks pass.

## Workspace Rule

File and terminal operations must stay inside the resolved workspace unless the
host explicitly permits another root. Authored code should not hard-code private
local paths.

## Secrets Rule

Provider keys, bot tokens, webhook secrets, SMTP credentials, and MCP secrets
belong in host config, environment variables, or `.env`. Authored slots and
tools should report secret sources, not secret values.

## Channel Rule

External channels validate inbound events before creating a turn. Examples:

- Telegram checks `allowed_users` and `allowed_chats`.
- Webhook checks token or `allow_unauthenticated`.
- Slack checks request signatures.
- Mattermost checks webhook tokens.
- Matrix checks homeserver credentials and optional room allowlists.
- Email checks credentials and optional sender/recipient allowlists.

The Agent Core does not gain network authority just because a channel is
enabled.

## Dependency Rule

Candidate Agent Cores and package recipes must not install Python dependencies.
Record dependency needs as manual review items.

## Boundary

A capability declaration is necessary for an effect, but it is not the whole
security decision. The host still applies workspace scope, command guards,
approval policy, channel policy, runtime task rules, and provider/tool runtime
rules.
