---
title: Runner and Context
description: Contributor notes for turn execution and provider context assembly.
---

# Runner and Context

The current alpha runner wires the turn lifecycle modules.
`TurnAdmissionRuntime` resolves the core/session route and starts the turn,
`TurnPipelineRuntime` runs the authored input -> model/tool -> output path, and
`TurnPersistenceRuntime` records input, assistant output, display state,
completion, and interruption. Agent Core slots participate through controlled
interfaces; they do not own the lifecycle.

This layout is the precursor to the frozen `TurnExecution`, `PrincipalScope`,
immutable `TurnExecutionContext`, and `ContextManager` interfaces. See
[Host Runtime Contracts](runtime-contracts.md) for the authoritative target
contract. The current runner does not yet satisfy every invariant below.

## Turn Flow

The current flow is:

```text
inbound interaction
  -> admit turn: resolve session/core, bind route, run bootstrap, begin turn
  -> run authored input pipeline
  -> assemble provider context
  -> call provider
  -> execute tool calls through ToolRuntime
  -> continue model/tool loop until final response
  -> run authored output pipeline
  -> persist input, assistant output, display state, completion, and session events
```

## Target TurnExecution Interface

The external Host seam is deliberately small:

```text
TurnExecution.run(TurnRequest) -> TurnResult
TurnExecution.cancel(TurnId, PrincipalScope) -> CancelResult
```

`TurnExecution` must hide session admission, core-revision pinning, context
preparation, provider/tool steps, slot execution, persistence, delivery, and
cleanup. A caller supplies immutable request values, not a mutable runner,
loaded core, store, provider client, or capability facade.

The module owns these observable contracts:

- same-session turns are serialized by admission, while different sessions can
  run concurrently;
- the session, core revision, capability snapshot, route, and trace identity
  captured for a turn do not change after an await;
- provider, slot, effect, cancellation, and unexpected failures create one
  terminal turn state before resources are released;
- restart marks or recovers orphaned admissions explicitly and never silently
  replays a dangerous provider/effect step;
- detached work is a separately owned runtime task, not a late mutation of a
  completed turn.

The current containment implementation enforces one in-process active turn per
session with a keyed admission lock; different sessions still run concurrently.
Admission captures the resolved session before bootstrap, and the prompt, IO,
slot history/result, event, artifact, and delivery hot paths use that captured
session or the immutable `TurnContext.session_id` after an await. Cancellation
and failure release the admission lock in `finally`. Admission also resolves a
frozen `PrincipalScope` before bootstrap: external conversations are matched
against the durable `session_owners` projection, TUI runs use explicit local
operator authority, schedules use run-scoped system authority, and child agents
own only their delegated child session. The scope is carried by the internal
`TurnExecutionScope`; it is not added to the authored `TurnContext` SDK.
Background tasks capture a bounded record of that admitted scope before their
detached task starts. Completion intake restores and validates the record
against the durable session owner before claiming the event; route metadata
cannot elevate the completion, and the internal scope record is not exposed in
model-facing metadata. Child spawn closures likewise capture the admitted
parent scope instead of reconstructing authority from a legacy session row.

This is not yet the final durable `TurnExecution` contract. Admission locks are
process-local, the scope still carries mutable objects, restart recovery is not
implemented here, and core-revision/route/cancellation ownership is completed
by the later TurnExecution work. PrincipalScope consumers are also being moved
incrementally: store-owned session/message/task predicates and same-origin
manual resume exist now, while session listing/search, task control, and
approval-cache enforcement remain assigned to their later DG-P2 tasks.

## Principal and Execution Context

`PrincipalScope` is Host authority, not an Agent Core capability grant. It is
derived from authenticated channel/operator/system facts plus durable
conversation/session bindings, and it supplies the owner predicate for session,
history, task, wait, cancel, resume, search, and approval-cache operations.

External adapter facts enter the Host through `InteractionInbound.principal_key`.
That field is set by the adapter after its transport authentication/allowlist
step and is kept separate from delivery `source`, arbitrary metadata, and raw
webhook body identifiers. A conversation scope is accepted only when that key,
channel, conversation binding, and session owner all match durable state.
The store-bound resolver is the only issuer. Operator issuance never accepts a
caller-supplied session set: it binds one active session, requires the active
Host's in-memory operator issuer plus an explicit reason, and writes a
`principal_scope.operator_issued` audit event. Cross-session operator queries
use a relational `session_owners` predicate rather than materializing an
unbounded SQL `IN` list. A scope issued by another store instance is rejected
at owned-query and session-persistence boundaries. Closing the Host revokes its
process-local operator capability even when tool shutdown fails, so a retained
scope cannot authorize reads after `DemiurgeApp.close()`.

`TurnExecutionContext` binds that principal to one session, turn, core revision,
capability snapshot, workspace, route token, admission lease, cancellation
token, and trace. Those bindings are immutable for the turn. Agent Slots and
authored tools continue to receive the reduced author-facing SDK contexts;
where applicable those contexts contain `TurnContext`. They do not receive
operator authority, Host stores, or admission internals.

## Context Layers

Provider context can include:

- soul text
- skill index and loaded skills
- bootstrap output
- input module placements
- session history
- current user turn
- tool call and tool result history

The current `ContextAssembler` decides final provider message order and content.
It does not know the model context window, reserve an output budget, or trigger
automatic compaction.

The target `ContextManager.prepare()` owns layer budgets, full-request
estimation, cheap pruning, compaction lease and fallback, and typed overflow
before provider IO. `ContextManager.observe()` consumes normalized usage and
finish-reason observations without relying on ambient mutable session state.
Manual `/compact` remains the current alpha mechanism until that module is
implemented.

## Bootstrap

Bootstrap modules are session-start context producers. They should be stable
within a session and safe to quote as reference context.

## Background Task Completion Turns

Background task completion is modeled as a synthetic inbound event for the
originating session rather than as direct channel output. Channel bridges use
live subscription as a wakeup path and recover pending completion events from
SQLite. If user input and completion are both pending, the user input runs first
and pending completion summaries are merged into that user turn. Completion
notifications use durable work state:
`ready` work is claimed before a bridge queues or merges the synthetic inbound,
and it is acknowledged only through the task-worker seam. A successful
`yield_until` call claims and acknowledges the matching pending completion, so
channel bridges do not run a second synthetic completion turn for the same task
result.

Parallel input and output slots are still scheduled concurrently, but the
runner waits for their host-managed work to finish before marking the parent
turn terminal. Detached slot work must be modeled as a child runtime task rather
than mutating after the parent turn is complete. Here **parallel** means
concurrent-but-joined within the parent turn; it is not detached or
restart-durable.

`/stop` and foreground cancellation affect only the active turn. Background
tasks continue until they finish or a user calls `task_control(command="cancel")`.

Background work that needs user input is marked `blocked_needs_user` and is
not auto-approved.

## Session Delivery Routes

The runner owns a shared `SessionInteractionRouter`. `InteractionRuntime`
passes the current adapter as a `SessionRouteBinding`; after the runner resolves
the final session for the inbound, admission captures that session id and binds
the route to the captured value.
TUI and channel `/new`, `/resume`, and session switch paths must rebind the
same adapter route to the new session.

External channel conversations also have a durable binding keyed by
`(core_id, channel, conversation_key)`. `conversation_key` is a canonical
host-owned route key built from explicit platform facts, for example
`telegram:dm:123` or `slack:channel:T1:C1:thread:123.4`. Channel `/resume`
rebinds the current conversation key to the resumed session so the next inbound
message from that external conversation continues in the same transcript.

The containment path now builds delivery from the captured turn session rather
than rereading `runner.session_id`. The final contract still moves the route
token itself into `TurnExecutionContext` so restart, owner checks, and route
lifetime are represented by one durable execution interface.

Ordinary output, tool lifecycle events, and background output flushes create
`InteractionOutbound` objects with a required `session_id`. The router delivers
only to the route bound for that session. If no route is bound, items are marked
`unrouted` and are not treated as failed adapter calls.

## Subagent Sessions

`ctx.agents.run()`, `ctx.agents.spawn()`, and `delegate_task` run child agents
in independent `session_child_*` sessions. Child runners share the same router
table but do not receive the parent route binding. Their ordinary output and
tool lifecycle delivery appear only on a route explicitly bound for the child
session.

Parent/child lineage remains task and observability metadata. It is not part of
ordinary delivery routing. Parent turns receive child work through
`AgentRunResult`, durable task completion, or explicit future `subagent.*`
events.

## Approval and Prompts

Interactive prompts and approval decisions use session-aware lookup on the same
router, but they are not ordinary delivery. By default an approval request is
looked up by `turn.session_id`; when no interactive route is bound, the approval
provider denies with `no_interactive_route` unless a host, global, or core
policy has already auto-allowed the action.

The current session-allow cache is not yet principal/session scoped. The target
cache key and lookup are owned by `ApprovalRuntime`, which consumes an immutable
`PrincipalScope`. Route lookup alone is not authorization.

## Failure Handling

In the current alpha runner, slot `failure_policy` determines whether a failed
slot is soft or hard. Exceptions/cancellation inside the guarded input,
provider/model-loop, and output stages write terminal turn state before being
re-raised. Tool-catalog preparation currently sits outside those guarded
regions, so its failure does not yet have the same guarantee. A foreground turn
is not a `RuntimeTask`; channel delivery, background task, and schedule errors
remain owned by their respective Host modules.

The target `TurnExecution` interface returns typed failed/cancelled product
outcomes and exposes only typed rejection or infrastructure failures. Adapter
exceptions do not become part of its caller interface.

## Boundary

Do not move provider request construction, context budgeting, principal
authority, or session ownership into Agent Core code.
