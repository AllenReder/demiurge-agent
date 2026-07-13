---
title: Tool Runtime
description: Contributor notes for tool discovery, metadata, dispatch, approvals, and results.
---

# Tool Runtime

The current `ToolRuntime` contains the first frozen Host-owned `EffectRuntime`
slice: one resolved per-turn catalog and one adapter-bound dispatcher for
builtin, authored, and MCP model calls. Adapter results are normalized into a
minimal typed `EffectResult`/`EffectError` before the turn loop converts them to
the legacy model-facing `ToolResult`; runtime events retain typed status/error.
Connect policy, extended lifecycle
outcomes, process/network lifecycle, output limits, and redaction continue
through later EffectRuntime work. See
[Host Runtime Contracts](runtime-contracts.md#effectruntime).

## Registry Sources

Tools can come from:

- built-in toolsets
- authored tools under `agent/tools/`
- MCP tools discovered from `agent/mcp/*.yaml`

`agent.yaml` chooses built-in toolsets and can override tool metadata.

## Current Dispatch

The current runtime resolves one immutable `ResolvedEffectCatalog` per turn.
Provider definitions, `tools_list`, capability and approval metadata, and
dispatch all use entries from that catalog. `TurnEngine` converts a provider
tool call into an `EffectRequest` containing the exact resolved entry; dispatch
does not search builtin definitions, authored slots, or the global MCP name
index again. Each entry binds source kind, core revision, adapter key, schema,
capability, effective approval policy, risk, and provenance.

The common dispatcher validates the core snapshot and applies the resolved
capability before selecting the builtin, authored, or MCP adapter. Dynamic
builtin checks such as workspace sensitivity and command review still refine
the resolved policy, while authored and MCP calls retain their adapter-specific
approval summaries. Core/global approval is folded into the catalog and can
only make policy stricter. Approval requests carry a bounded,
field-name-redacted argument preview. This containment is not the final
cross-effect `SecretRedactor` owned by the later `EffectRuntime`/SEC-02 work.

`ToolRuntime.execute()` accepts only an `EffectRequest` owned by its catalog and
returns a typed `EffectResult`. Direct Host callers use
`SessionTurnStepRunner.execute_call()` to resolve once, or pass an existing
request through `execute_tool()` for an explicit legacy conversion; there is no
bare-`ToolCall` execution fallback.

Tool names are unique across sources. The core loader rejects builtin/authored
collisions, and final catalog construction rejects collisions involving MCP
tools. Errors include both provenances and require the authored or MCP tool to
be renamed; there is no implicit builtin priority.

MCP discovery is also prepared before model execution. On a catalog cache miss,
normal `TurnExecution` now requires `mcp.connect:<server>` and resolves connect
approval before client construction or `list_tools()`; the later tool call has
its own `mcp.call:*` gate. Call dispatch remains bound to the current
turn/session entry. `list_tools()` is bounded per server by
`connect_timeout_seconds`, and a timed-out server is closed without blocking
later servers. Discovery uses one runtime-wide limit of four concurrent server
operations across sessions and assembles names deterministically afterward.
Current failure diagnostics use a per-server 30-second negative-cache TTL;
within one catalog authority, expiry retries only the failed server while
healthy peers stay connected, and authority denial is rechecked per server on
the next turn. Per-server manifest fingerprints support targeted reconnects
only while the overall authority/core snapshot is unchanged. Catalog identity
binds principal, capability snapshot, core revision, and effective connect
policy; any such change evicts the whole stale catalog before reuse. Configured cwd
is validated against the Host workspace before approval/client construction.
Declaration changes also require connect reapproval before a replacement client
starts; removing all declarations closes the remaining connections. Starting
or resuming another session tracks eviction of the previous session. Explicit
session eviction closes only the selected session's catalogs;
delegated children use their Host-issued scope and release connections at child
completion. Terminal subprocesses now use an allowlisted environment and
one-shot capability/approval/expiry-bound secret injection; MCP stdio children
reuse that allowlist and add only approved manifest env entries. URL validation
remains later security work. The legacy global
MCP tool-name index has been removed;
call dispatch accepts only the connection-bound resolved entry.

Terminal preflight classifies project-code execution separately from literal
read-only commands, requires approval for explicit environment overlays, and
constructs an audit view containing actual cwd, environment keys, resolved
shell/process and best-effort command executables, and secret-binding metadata. Secret values are
resolved only after approval and exact values are redacted from returned
stdout/stderr. Background terminal tasks reject secret bindings until their
process/expiry lifecycle can provide the same guarantee.

Secret capabilities use exact-default lookup rather than the normal prefix
wildcard matcher. Binding targets reject execution-control variables, and the
earliest binding deadline clamps the foreground `subprocess.run()` timeout.

## Target EffectRuntime Interface

The external Host seam is:

```text
EffectRuntime.execute(EffectRequest, TurnExecutionContext) -> EffectResult
```

The immutable per-turn catalog produces both provider-visible definitions and
an opaque resolved effect reference. Execution must use that same reference;
it must not perform a second global name lookup.

Every builtin, authored, and MCP effect follows one order:

1. validate the request and resolved catalog binding;
2. enforce `PrincipalScope` visibility and owner rules;
3. require the immutable capability snapshot;
4. run pure workspace, command, URL, process, environment, namespace, and
   output checks;
5. resolve approval;
6. bind only explicitly authorized secrets;
7. invoke the selected adapter under deadline and cancellation;
8. clean up, bound streaming output, redact, and produce separate model,
   operator, event, and durable views.

For Host-mediated model-triggered effects, no authored tool import/invocation,
subprocess spawn, MCP connect/discovery, file mutation, or network effect may
precede its applicable capability and approval checks. This does not claim
control over direct Python/OS calls from already imported `host_shared` Slot
code. `mcp.connect:<server>` and `mcp.call:<server>` are distinct effects.

## Background Tasks

`ToolRuntime` does not own background state. Background-capable tools submit
typed actions to the host runtime and use the shared `RuntimeTaskWorker` as the
live worker for active work:

- `terminal(background=true)` creates a `terminal.exec` task and captures
  stdout/stderr into `task_logs`.
- `evolve_core(action="start", background=true)` creates an `evolver.run` task
  that edits an isolated agents-tree worktree. It returns a task id; the
  completed task metadata/result identifies the evolve run. It does not switch
  the live core.
- `evolve_core(action="review")`, `evolve_core(action="promote")`, and
  `evolve_core(action="discard")` operate on that run id through the host-owned
  evolution runtime. Promotion advances Git refs only after gates pass. An MCP
  declaration change adds a secret-safe command/argument, URL, cwd,
  environment/header-name, risk, approval, and capability diff to the review;
  promotion remains blocked until that manual security review is confirmed by
  the successful promote approval. Every
  action resolves capability and approval before dispatch; the action and
  target are part of the approval-cache rule so one mutation action does not
  authorize another. The cache additionally binds the admitted principal,
  session, core revision, capability snapshot, effective policy, and effect
  entry. Successful promotion or rollback invalidates cached authority for the
  affected core. `EffectRuntime` must remove the remaining dispatcher
  duplication without weakening this ordering.
- `ctx.agents.spawn(...)` is routed by the runner into an `agent.spawn` task.
- `delegate_task(...)` is executed by the active runner context and creates an
  `agent.spawn` task with child output returned as parent evidence.
  Both paths record requested and resolved child input/output slot and tool
  selection in task metadata.

`task_list`, `task_status`, `task_control`, and `yield_until` are the
model-facing runtime-task controls. `task_control` supports only
`command="cancel"`; non-cancel commands are rejected as unsupported. Active
execution is still live in the host runtime, while task status and logs are
stored in SQLite projections through `RuntimeControlPlane`. Detail, wait,
completion consumption, and cancel use the admitted `PrincipalScope` in the
store query. Model-facing task controls return bounded status/result fields and
cannot request `operator` or `debug` views; full task logs remain available only
through the independent Host/operator surface. Model payloads omit owner ids,
write scope, arbitrary metadata, result references, and logs; summaries are
bounded. `task_list` uses the same model projection and a store-side owned query
restricted to the current turn session. `/subagents` uses the full operator
projection only after the same owner check; guessing another principal's task
id returns the same result as a missing id.
Runner-owned delegation controls call the same `resolve_approval_scope(...)`
seam as ordinary ToolRuntime dispatch, so they cannot apply a weaker execution
identity check.

`session_search` requires `session.read` and the resolved `prompt/medium`
approval policy before any history read. Browse, explicit-session, and full-text
paths use `SessionRuntime` owned list/message queries. Ordinary conversation
scope is limited to its bound session; an audited operator scope can search all
normally owned sessions after approval. Ambiguous `legacy_local` sessions are
excluded and require the dedicated operator repair/status path.

Each background task records `kind`, owner session/turn, `source_tool`,
status, summary, bounded log tail, result reference, and an optional
`write_scope`. A new active background task with the same non-empty
`write_scope` is rejected.

## Authored Tools

Authored tools are EffectRuntime adapters. They share the resolved per-turn
catalog with builtins and MCP tools, and dispatch enforces singular registry
capability/approval metadata before import/invocation. Their
`capabilities` list remains a separate grant surface for explicit
`ctx.capability.require(...)` checks and cannot self-grant the singular dispatch
gate. Later EffectRuntime work extends typed lifecycle outcomes and adds
output/redaction policy and adapter lifecycle without reopening name-based
dispatch.

## MCP Tools

MCP tools receive normalized server-prefixed names and include/exclude filters.
Transport, discovery, timeouts, and result conversion are Host-owned. Each
visible MCP definition is now bound to the current session/revision connection
and one resolved effect entry, so call dispatch never falls back to the global
tool-name index. Connect/discovery uses a separate pre-client capability and
approval gate; session/authority/declaration changes evict stale connections.

## Boundary

The Agent Core can declare tools. It does not own tool-call replay,
principal authorization, or provider-specific tool message formatting.

`host_shared` authored Python is not a sandbox. Centralizing model-triggered
effect policy does not prevent imported Python from using ordinary Python or OS
APIs; optional subprocess/per-core isolation is a later adapter at the same
Host seam.
