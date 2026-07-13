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
Host terminal runtime. Terminal subprocesses now start from a Host allowlist
instead of inheriting the full process environment, use a dedicated runtime
`HOME`, and omit provider, channel, MCP, cloud, and desktop credentials by
default. Commands that execute workspace/project code, plus any explicit
environment overlay, require approval even when their outer command is a known
development command. Process-tree control remains a separate boundary.

## Capabilities

Capabilities describe effect classes such as:

- `fs.read`
- `fs.write`
- `terminal.exec`
- `secret.bind:<ENV_NAME>`
- `task.control`
- `network.fetch`
- `schedule.manage`
- `tool.call:evolve_core`
- `tool.call:rollback_core`

Builtin file, terminal, network, schedule, and skill handlers resolve their
applicable capability/approval checks before guarded operations, and MCP tool
calls do so before the call. Authored tool dispatch now requires the resolved
singular capability and approval policy before module import/invocation. Alpha
gaps remain: MCP spawn/connect/discovery can occur before call approval, while
builtin/authored/MCP dispatch still uses separate implementation branches.
`evolve_core` / `rollback_core` now use the same resolved registry entry for
capability and monotonic approval policy before adapter calls or background
task creation. The target `EffectRuntime` removes the remaining branch
duplication with one ordering; see
[Host Runtime Contracts](../developer-guide/runtime-contracts.md#effectruntime).

Background completion turns use the originating session's normal capabilities
and do not gain approval merely by running in the background. An
`evolve_core(action="start", background=true)` request must pass its resolved
capability and action-specific approval before the Host creates the runtime
task.

## Secrets

Provider secrets belong in host config, environment variables, or
`~/.demiurge/.env`. Status commands should report secret sources without
printing secret values.

The terminal does not inherit those values. A foreground call can request a
one-shot `secret_bindings` entry sourced from `env:<NAME>` only when the active
capability snapshot grants `secret.bind:<NAME>`. The Host prompts, bounds the
binding by the terminal timeout, rejects background use, records only
source/target/capability/expiry metadata, and replaces exact bound values in
stdout/stderr with a redaction marker. This is controlled injection, not a
sandbox or a guarantee against transformed/encoded disclosure.

The capability must be exact (`secret.bind:*` does not match), and a binding
cannot override `PATH`, `HOME`, shell/loader controls, or language runtime
search paths after approval. The earliest binding expiry shortens the
foreground subprocess timeout; descendant process-tree cleanup remains a
separate lifecycle boundary.

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
authority. Approval caching now enforces the admitted `PrincipalScope`, session,
core/capability policy fingerprint, bounded lifetime, and explicit revocation;
tool arguments cannot declare another owner. Session browsing/resume/search and
task detail/wait/cancel now enforce the same scope in store-owned queries;
`session_search` additionally requires `session.read` plus approval. The later
unified EffectRuntime still needs this seam across every effect adapter before
the alpha runtime provides uniform enforcement. Runtime task records,
logs, scheduler instances, and delivery outbox status are stored in the SQLite
runtime database; in-process workers are still responsible for live execution
and do not replay already started dangerous side effects after host process
restart.

Ambiguous migrated sessions use the `legacy_local` owner kind. Normal channel
and operator session/history queries fail closed for those rows; inspection is
reserved for the explicit operator repair/status path. Model-facing task tools
also cannot select operator/debug views or receive task logs.
The repair/status path is Host-only, requires an exact lookup plus a bounded
operator reason, and writes a durable audit event. Failed exact owned lookups
also retain their true reason in Host audit while preserving one indistinguishable
external error.
