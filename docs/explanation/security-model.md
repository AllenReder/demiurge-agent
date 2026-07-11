---
title: Security Model
description: Understand workspace scope, approvals, capabilities, secrets, and channel trust.
---

# Security Model

Demiurge treats capabilities and dangerous model-triggered effects as
Host-owned. The supported `ctx.*`, builtin-tool, and MCP-call paths request
effects through Host interfaces. In the default `host_shared` runtime, imported
Agent Core Python is trusted code and can also use ordinary Python/OS APIs; the
current alpha runtime is not a sandbox.

## Workspace Scope

File writes, patches, and terminal working directories are scoped to a resolved
workspace. The workspace can come from a process override, environment variable,
core manifest, local run context, or the fallback `~/.demiurge/workspace`.

Built-in file reads can target host-visible paths outside the workspace. Those
outside-workspace reads, and all sensitive reads, require approval before the
file is opened.

Workspace scope is not the only guard. Sensitive paths and dangerous operations
can still require approval or be rejected.

## Terminal Command Containment

The terminal command guard evaluates the execution-faithful raw command plus
additive ANSI/NFKC detection candidates. Literal `allow/low` commands may use
automatic approval. Executable or unmodelled shell expansion, nested shell
evaluation, malformed shell syntax, and unknown commands remain `prompt/high`;
global `auto` policy cannot weaken that result. Known destructive hardline
payloads are blocked before approval.

This scanner is deliberately fail-closed and may prompt for ambiguous text,
including expansion-like syntax inside comments. It is containment, not a full
shell parser or sandbox. Explicitly approved commands still execute in the
Host terminal runtime; environment sanitization, process-tree control, and
principal-scoped approval remain separate security boundaries.

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

Builtin file, terminal, network, schedule, and skill handlers resolve their
applicable capability/approval checks before guarded operations, and MCP tool
calls do so before the call. Authored tool dispatch now requires the resolved
singular capability and approval policy before module import/invocation. Alpha
gaps remain: MCP spawn/connect/discovery can occur before call approval, and
`evolve_core` / `rollback_core` require capabilities but do not yet resolve
their registry `prompt` policy before mutating core refs. The target
`EffectRuntime` closes these paths with one ordering; see
[Host Runtime Contracts](../developer-guide/runtime-contracts.md#effectruntime).

Background completion turns use the originating session's normal capabilities
and do not gain approval merely by running in the background. The current
`evolve_core` registry-policy gap also affects background start, so the alpha
runtime does not yet guarantee that every dangerous background action reaches
approval before execution.

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
the default runtime mode. Capability grants do not confer session/operator
authority. In the frozen target, owning session, task, approval, and effect
modules will enforce predicates carried by `PrincipalScope`; the current alpha
runtime does not yet provide that uniform owner scope. Runtime task records,
logs, scheduler instances, and delivery outbox status are stored in the SQLite
runtime database; in-process workers are still responsible for live execution
and do not replay already started dangerous side effects after host process
restart.
