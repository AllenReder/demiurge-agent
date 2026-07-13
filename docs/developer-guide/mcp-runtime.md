---
title: MCP Runtime
description: Contributor notes for MCP server discovery, naming, transports, and result conversion.
---

# MCP Runtime

The MCP runtime discovers server declarations from Agent Cores and exposes
filtered tools through the host tool registry.

In the current alpha runtime, normal `TurnExecution` catalog preparation first
requires `mcp.connect:<server>` and resolves the server risk/approval policy.
Denied or missing authority skips the server before client construction and
`list_tools()`. A later model call separately requires `mcp.call:<server>` (or
the manifest's explicit call capability) and call approval. See
[Host Runtime Contracts](runtime-contracts.md#effectruntime).

## Discovery

Declarations live under:

```text
agent/mcp/*.yaml
```

Disabled declarations are ignored. Stdio declarations require `command`.
Streamable HTTP declarations require an `http://` or `https://` URL.

## Naming

Tool names are normalized, server-prefixed, and filtered before they become
visible. The per-turn resolved catalog binds each visible MCP tool to its
session/revision connection and dispatcher adapter. Calls therefore use that
connection-bound entry instead of the legacy global name index. Cross-source
name collisions fail with both provenances. Namespacing still does not replace
the independent connect and call authority checks.

## Environment and Headers

Declarations can provide environment variables, headers, cwd, timeouts, risk,
approval policy, and parallel-call support. Secrets should come from the host
environment. Interpolation now occurs only after connect capability/approval,
and configured cwd must resolve inside the Host workspace before approval and
client construction. The approval preview shows the command, cwd, option shape,
environment/header names, and a credential-free URL; positional values are
represented by hash/length summaries and secret-bearing option values are
redacted. Stdio subprocesses always use the shared Host environment allowlist
and a dedicated runtime `HOME`; only declaration-listed `env` entries are added
after connect approval. Shared URL policy remains later security work.

## Result Conversion

MCP results are converted into Demiurge tool results before model replay and
display.

## Boundary

The core declares MCP servers. The host owns transport lifecycle, discovery,
timeouts, policy, and tool execution. `list_tools()` is bounded per server by
`connect_timeout_seconds`; timeout closes that connection, records a diagnostic,
and continues to later servers. Discovery uses a runtime-wide Host-owned
maximum of four concurrent servers across sessions while preserving
deterministic catalog naming. Failure diagnostics are cached per server for 30
seconds; within the same catalog authority, expiry retries only that server
while healthy peer connections remain published. Authority denial is also
rechecked per server on the next turn rather than becoming a negative cache.
Each connection identity includes that server's own manifest fingerprint, so a
same-authority refresh can reconnect only the changed server. A principal,
capability, core-revision, workspace, or effective-policy change evicts and
reauthorizes the whole stale catalog instead of reusing peers across snapshots.
Removing every declaration
closes the remaining session connections. Catalog identity also binds the principal,
capability snapshot, core revision, and effective connect policy, so tightened
authority cannot reuse an older connection. Starting or resuming another
session schedules tracked eviction of the previous session; explicit eviction
still closes only the selected session's catalogs. Delegated children prepare
with their Host-issued scope and release their MCP connections when the child
run ends. Evolution review emits a secret-safe before/after security diff for
changed MCP declarations and a content-bound `mcp-review:<sha256>` token.
Promotion requires that exact token in addition to the normal promote approval;
missing or stale tokens leave Git refs unchanged. Stdio env sanitization and
declaration-bound secret injection are implemented; URL validation remains a
later security layer.
