---
title: Tool Runtime
description: Contributor notes for tool discovery, metadata, dispatch, approvals, and results.
---

# Tool Runtime

The current `ToolRuntime` builds the visible tool registry and executes calls.
It is the precursor to the frozen Host-owned `EffectRuntime` interface, but the
alpha implementation does not yet provide one policy/dispatch path for every
effect. See [Host Runtime Contracts](runtime-contracts.md#effectruntime).

## Registry Sources

Tools can come from:

- built-in toolsets
- authored tools under `agent/tools/`
- MCP tools discovered from `agent/mcp/*.yaml`

`agent.yaml` chooses built-in toolsets and can override tool metadata.

## Current Dispatch

The current runtime resolves a model tool name and then takes separate builtin,
authored, or MCP branches. Many builtin handlers apply their own capability,
approval, workspace, command, and network checks, and there is still no generic
builtin gate. The core-mutation branches now receive the same resolved registry
entry used for visibility, require its singular capability, and apply its
monotonic approval policy before any evolution/version-store adapter call or
background task creation. MCP call dispatch applies its call capability and
approval policy. Authored tool
dispatch uses the same resolved registry entry exposed to the model/operator,
requires its singular `capability`, and resolves `risk`/`approval_policy` before
the authored entrypoint is imported and called. Core/global approval can make
that policy stricter but cannot weaken it. Approval requests carry a bounded,
field-name-redacted argument preview. This containment is not the final
cross-effect `SecretRedactor` owned by the later `EffectRuntime`/SEC-02 work.

MCP discovery is also prepared before model execution. On a catalog cache miss,
the current runtime can spawn/connect and call `list_tools()` before the later
`mcp.call:*` capability and approval check. Registry display and execution can
then resolve MCP tools through different lookup state. These are known alpha
gaps, not supported extension points.

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
  evolution runtime. Promotion advances Git refs only after gates pass. Every
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

Authored tools are intended EffectRuntime adapters. Today they share registry
discovery with builtins, and their current dispatch now enforces singular
registry capability/approval metadata before import/invocation. Their
`capabilities` list remains a separate grant surface for explicit
`ctx.capability.require(...)` checks and cannot self-grant the singular dispatch
gate. The later `EffectRuntime` removes the remaining builtin/authored/MCP
dispatch duplication.

## MCP Tools

MCP tools receive normalized server-prefixed names and include/exclude filters.
Transport, discovery, timeouts, and result conversion are Host-owned, but
connect/discovery policy ordering and connection-bound dispatch are not yet
closed in the current alpha runtime.

The target catalog binds each visible MCP definition to one session/revision
connection and one opaque effect reference. A call never falls back to a global
tool-name index.

## Boundary

The Agent Core can declare tools. It does not own tool-call replay,
principal authorization, or provider-specific tool message formatting.

`host_shared` authored Python is not a sandbox. Centralizing model-triggered
effect policy does not prevent imported Python from using ordinary Python or OS
APIs; optional subprocess/per-core isolation is a later adapter at the same
Host seam.
