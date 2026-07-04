---
title: Security Model
description: Understand workspace scope, approvals, capabilities, secrets, and channel trust.
---

# Security Model

Demiurge treats capabilities as host-owned. Agent Core code can request effects
only through controlled interfaces.

## Workspace Scope

File writes, patches, and terminal working directories are scoped to a resolved
workspace. The workspace can come from a process override, environment variable,
core manifest, local run context, or the fallback `~/.demiurge/workspace`.

Built-in file reads can target host-visible paths outside the workspace. Those
outside-workspace reads, and all sensitive reads, require approval before the
file is opened.

Workspace scope is not the only guard. Sensitive paths and dangerous operations
can still require approval or be rejected.

## Capabilities

Capabilities describe effect classes such as:

- `fs.read`
- `fs.write`
- `terminal.exec`
- `task.control`
- `network.fetch`
- `schedule.manage`
- `tool.call:evolve_core`
- `tool.call:rollback_core`

The host resolves capabilities and applies approval policy before the effect
runs.

Background completion turns use the originating session's normal capabilities
and approvals. Background tasks do not auto-approve dangerous actions.

## Secrets

Provider secrets belong in host config, environment variables, or
`~/.demiurge/.env`. Status commands should report secret sources without
printing secret values.

Package component options of type `secret` can write component-local config, but
`packages.yaml` stores only redacted option values. Package provenance hashes in
that file are used for drift reporting and uninstall safety; runtime truth is
still the committed agents tree.

## Channels

External channels are disabled by default. Channel bridges must verify tokens,
signatures, allowlists, or room/user constraints before accepting inbound
events.

Telegram is deny-by-default through `allowed_users` and `allowed_chats`.

## Non-Goals

The current alpha runtime does not promise a hardened multi-tenant sandbox.
Agent Slot code runs in the host-shared Python environment by default.
Per-core environments and subprocess workers are future isolation options, not
the default runtime mode. Runtime task records, logs, scheduler instances, and
delivery outbox status are stored in the SQLite runtime database; in-process
workers are still responsible for live execution and do not replay already
started dangerous side effects after host process restart.
