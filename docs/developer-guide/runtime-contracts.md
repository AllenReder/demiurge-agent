---
title: Host Runtime Contracts
description: Frozen contributor contracts for turn, effect, context, principal scope, and durable channel ingress.
---

# Host Runtime Contracts

This page freezes the contributor-facing interfaces that later hardening phases
must implement. It is a design contract, not a claim that every invariant is
already enforced by the current alpha runtime. The regression tests introduced
before this page intentionally remain red where the implementation still falls
short.

These contracts preserve the product boundary:

- the Host owns the harness, authority, dangerous effects, persistence,
  delivery, promotion, and rollback;
- an Agent Core owns `agent.yaml + agent/`;
- Agent Slots remain the authored logic seam;
- `host_shared` remains the default authored Python runtime;
- candidate dependency changes remain manual review items;
- Git-backed core revision, promotion, and rollback remain Host-controlled.

## Contract Vocabulary

The terms on this page are deliberate:

- A **module** hides implementation behind one interface.
- An **interface** includes inputs and outputs plus invariants, ordering, error,
  cancellation, restart, and performance semantics.
- A **seam** is where callers and tests cross that interface.
- An **adapter** satisfies an interface at a seam.

The four Host modules below are external seams for Host callers. Their helper
objects, stores, transports, and test fakes are internal seams. Agent Core
authors do not call these interfaces directly; they continue to use the
reduced `ctx.*` SDK and model-visible tools.

| Module | Frozen external interface | Current implementation precursor |
| --- | --- | --- |
| `TurnExecution` | `run(TurnRequest) -> TurnResult`; `cancel(TurnId, PrincipalScope) -> TurnCancelResult` | implemented by `TurnExecution`; `SessionTurnStepRunner.run_turn()` is wiring |
| `EffectRuntime` | `execute(EffectRequest, TurnExecutionContext) -> EffectResult` | `ToolRuntime`, security helpers, `McpRuntime`, and inline process/network code |
| `ContextManager` | async `prepare(ContextRequest) -> PreparedContext`; async `observe(UsageObservation) -> None` | `ContextAssembler`, `PromptContextRuntime`, and `SessionCompactionRuntime` |
| `ChannelInbox` | `accept(InboundEnvelope) -> InboxReceipt`; `claim() -> ClaimedInbound`; `complete(...)`; `fail(...)` | no durable inbound owner yet |

The names, ownership, and behavioral semantics are frozen here. Exact private
class layout and storage schema remain implementation details.

## PrincipalScope

`PrincipalScope` is the immutable Host authority carried into session, task,
approval, effect, and history operations. It is not a capability grant and is
never constructed from untrusted payload fields alone.

The minimum logical fields are:

```text
principal_id
authority: conversation | operator | system | delegated_agent
channel
conversation_key
session_id
allowed_session_ids: frozenset
```

The Host derives the value from authenticated adapter facts plus durable
conversation/session bindings:

- a channel conversation normally owns exactly its bound session;
- CLI/TUI cross-session operations require explicit operator authority;
- a schedule receives system authority limited to its scheduled run/session;
- a child agent owns its child session, not its parent's session;
- `CapabilityFacade` describes Agent Core effect grants and never substitutes
  for principal authorization.

Only the store-bound Host authority resolver constructs `PrincipalScope`. Transport
adapters contribute authenticated facts; request payloads and Agent Core code
cannot instantiate operator/system authority. Legacy session/task rows with
missing or ambiguous ownership fail closed and are visible only to an explicit
operator repair path.

Operator scope issuance requires a non-empty audit reason and derives its
active-session binding from durable ownership; callers cannot inject
`allowed_session_ids`. It also requires an in-memory issuer held by the active
Host, so reopening the SQLite path does not enable operator issuance. Host
shutdown revokes that process-local capability in `finally`; retained operator
scopes are rejected after `DemiurgeApp.close()`, including when tool shutdown
raises.
Cross-session operator reads use a relational `session_owners` predicate and
do not build an unbounded SQL bind list. Owned queries or session persistence
reject a scope issued by another store.

The current alpha implementation places this seam in
`demiurge/runtime/scope.py`. `TurnAdmissionRuntime` now carries a frozen scope
for external conversations, the local TUI operator, scheduled runs, and child
agents. External adapters provide a Host-set `principal_key`; routing handles,
arbitrary metadata, and webhook body fields do not independently grant
authority. A background task captures a single-session origin-scope record at
task start. Completion intake restores that record through the same store-bound
resolver, verifies it against the completion owner before claim, and carries it
only on the internal inbound object; model-facing metadata does not contain the
scope record. A durable completion without that record fails before claim
instead of falling back to route facts. Delegated sessions persist parent session/turn lineage without
inheriting parent access, and child execution captures the admitted parent
scope before a detached spawn closure starts.

`RuntimeStore` schema version 5 adds the immutable `session_owners`
projection. Fresh sessions persist their owner with `session.created`.
Upgrading a version 4 database takes an integrity-checked backup first, safely
backfills an unambiguous conversation binding, and marks ambiguous rows
`legacy_local`; ordinary origin resolution never promotes those rows.
Migration failure leaves the version 4 database unchanged and reports the
absolute database/backup paths plus an explicit restore action. An existing
backup is reused only when its logical fingerprint matches the current version
4 database; a valid but stale backup stops migration.
`RuntimeStore.query_owned()` currently applies SQL owner predicates for
sessions, messages, and tasks, and `SessionRuntime` exposes owned get/list
interfaces. Approval requests now derive their owner and correlation from the
admitted `TurnExecutionContext`, or from an explicit Host-issued
`PrincipalScope` for non-turn operations. The remaining slash-command,
session-search, and task-control callers are intentionally migrated in the
next PrincipalScope task; their presence must not be read as complete owner
enforcement yet.

Every detail, list, wait, cancel, history, resume, search, and approval cache
operation applies its owner predicate in the owning module/store. Callers must
not fetch a global object by id and then perform an ad hoc owner check.

An `always_allow_for_session` cache key is at least
`(principal_id, session_id, policy_fingerprint, rule_key)`. The fingerprint
covers core revision, capability snapshot, effective approval policy, and the
relevant effect entry. Entries are invalidated on session end, authority or
conversation-binding change, policy/revision change, explicit revocation, or
bounded expiry. Session-scoped approval does not survive as ambient
process-wide authority.

The current implementation serializes concurrent decisions per complete cache
identity, rechecks the cache after admission, and removes cancelled waiters.
Its bounded TTL is eight hours. Starting a replacement session invalidates the
old session, Host shutdown clears all entries and rejects pending decisions,
and successful promotion or rollback invalidates the affected core. Approval
events and operator payloads use a bounded, field-name-redacted view; the
opaque `PrincipalScope`, capability snapshot, and raw secret argument values
are not serialized.

## TurnExecutionContext

`TurnExecutionContext` is created once, after admission resolves authority and
acquires the session lease. Its bindings are deeply immutable for the duration
of one turn. A frozen dataclass containing mutable dicts, lists, `LoadedCore`,
or a mutable runner reference does not satisfy this contract.

The minimum logical bindings are:

```text
request_id
turn_id
principal: PrincipalScope
session_id
core_id
core_revision
capability_snapshot
workspace_scope
route_token
admission_lease_token
cancellation_token
trace_ids
interaction_metadata
```

The lease and cancellation fields are immutable identities, not mutable control
objects. The owning Host modules keep the live lease and monotonic cancellation
state behind internal seams; callers cannot mutate them through the context.

The bindings must agree: `principal` authorizes `session_id`; the capability
snapshot belongs to the pinned `core_id`/`core_revision`; route, admission, and
cancellation tokens all name the same request/turn/session and cannot be reused
across turns. `interaction_metadata` is a bounded, redacted, deeply immutable
value rather than a transport-owned mutable dict.

Secret values, mutable stores, provider clients, approval caches, and adapter
implementations are not fields on the public context. They remain injected
dependencies of the owning Host module.

Agent Slots and authored tools continue to receive the existing reduced
author-facing SDK contexts; where applicable those contexts contain
`TurnContext`. They do not receive `PrincipalScope`, route internals, admission
leases, Host stores, or operator authority.

## TurnExecution

### Interface

```text
TurnExecution.run(TurnRequest) -> TurnResult
TurnExecution.cancel(TurnId, PrincipalScope) -> TurnCancelResult
```

`TurnRequest` contains deeply immutable values only:

- raw input and bounded attachments;
- authenticated principal and conversation facts;
- a session selector: resolve the bound session, create a fresh session, or
  request an owner-authorized resume;
- core id and an optional explicitly pinned revision for child/evolver runs;
- optional immutable Host route identity; the live route adapter remains an
  injected dependency;
- immutable input/output slot selection and injected context;
- bootstrap flag and stable request/idempotency key.

It does not contain `LoadedCore`, `CapabilityFacade`, state/history stores,
event logs, provider clients, or a mutable runner.

`TurnResult` is a frozen snapshot:

- session, turn, core, and pinned revision ids;
- terminal outcome, including `needs_user` when interaction must pause for user
  input;
- immutable delivery/tool-result summaries;
- agent result and durable result reference.

It must not expose interaction objects whose dispatch status can mutate after
return.

Expected product outcomes are returned, not leaked as adapter exceptions:

```text
completed | needs_user | failed | cancelled | lost | indeterminate
```

Validation, authentication, or admission failure before `turn.started` raises
a typed `TurnRejected` with a bounded reason and no cross-owner existence leak.
Provider, slot, and effect failures after start are sanitized, persisted, and
returned as `TurnResult(outcome="failed")`. Explicit cancellation returns a
cancelled result. Ambient host coroutine cancellation records the cancelled or
indeterminate terminal outcome and then re-raises cancellation. A storage or
invariant failure that prevents a trustworthy terminal record raises typed
`TurnInfrastructureError(request_id, durable_result_ref?, outcome="indeterminate")`;
callers must not infer success or retry non-idempotent work from it.

### Ordering and Invariants

One call owns this order:

1. Validate and deeply freeze the request before side effects.
2. Authenticate the principal and atomically resolve/create the durable
   conversation binding.
3. Acquire a per-session admission lease before the first awaitable authored or
   provider operation.
4. Pin core revision, capability snapshot, workspace, route, and trace ids.
5. Persist `turn.started`.
6. Ask the internal EffectRuntime catalog seam to finalize the immutable
   per-turn definitions and resolved references.
7. Run bootstrap and input slots against that same catalog, then persist
   normalized input.
8. Pass the catalog's final definitions to `ContextManager.prepare()`, then run
   provider/effect steps and output slots.
9. Commit terminal turn state and durable delivery intents.
10. Return an immutable result and release catalog, route, and admission
    resources in `finally`.

Required invariants:

- same-session turns are serialized inside the module;
- different sessions remain concurrent; there is no process-wide turn lock;
- session switching changes future requests only;
- every downstream operation uses `TurnExecutionContext`, never a mutable
  "current runner session";
- core revision and capabilities cannot change mid-turn;
- delivery uses the captured session/route identity;
- every exit after `turn.started` creates exactly one terminal state;
- foreground completion rejects late slot/tool writes;
- detached work is a separately owned runtime task, not a late mutation of the
  parent turn.

The transactional runtime store is the lifecycle source of truth. Admission
persists the request/idempotency key and `turn.started` atomically with the
resolved session binding/lease. Terminal turn state and durable delivery/outbox
intents commit in one transaction. Event logs, display state, and live route
delivery are projections or consumers of those records, never competing
completion authorities.

### Errors, Cancellation, Restart, and Performance

- Validation/authority failures happen before `turn.started`.
- Provider, slot, and effect failures persist and return a failed terminal
  result without exposing adapter exceptions.
- Cancellation is owner-checked and idempotent, persists a cancelled terminal
  turn, releases admission, and does not cancel detached tasks implicitly.
- Restart expires or recovers admission leases and marks orphaned running turns
  explicitly lost/failed/cancelled; it never silently replays a dangerous
  provider/effect step.
- Admission lookup is keyed and effectively O(1); idle lock entries are
  removed; session/task owner queries are indexed, bounded, and paginated.

The current runtime implements the external `TurnExecution` seam and a frozen
`TurnExecutionContext`. Admission pins principal, session, loaded core and
revision, an immutable capability declaration snapshot, route token, trace id,
cancellation identity, and an admission-lease identity. Live core, lifecycle,
state, lock, and task controls remain private admitted-turn state. Same-session turns
serialize, different sessions remain concurrent, idle lock entries are
removed, and owner-checked cancellation releases admission.
Queued requests form a consistent post-admission core/revision snapshot; a
promotion while waiting cannot label pre-promotion content with the new ref.

The admitted route is execution-local Host state, not interaction metadata.
Delivery, prompt, approval, and asyncio tasks created during the turn resolve
the exact captured token; route rebinding affects future turns only, and an
unbound captured token fails closed. `SessionTurnStepRunner` is wiring around
this module rather than an alternate lifecycle implementation. Delivery tasks
are tracked per turn and drained before the active-turn registry entry is
released, so cancellation during adapter delivery finalizes its claim and does
not wait on unrelated session deliveries. Earlier provider/tool/slot failures
cancel and await any already scheduled interim deliveries in the same cleanup.

This remains short of the full target above: admission and active cancellation
are process-local, restart recovery and durable admission leases are later
work, `TurnResult` has not yet migrated to the complete typed outcome snapshot,
and mutable lifecycle/state handles remain internal implementation objects.

## EffectRuntime

### Interface

```text
EffectRuntime.execute(EffectRequest, TurnExecutionContext) -> EffectResult
```

`EffectRequest` contains:

- a unique call/request id;
- an opaque resolved effect reference from the immutable per-turn catalog;
- deeply frozen arguments;
- invocation origin such as model, authored slot/tool, or Host.

The per-turn catalog produces both provider-visible definitions and the opaque
resolved reference. `execute()` never performs a second global name lookup.
Capability, workspace, principal, approval, secret values, and adapter choice
are not caller-supplied request fields.

### Internal Catalog Seam

Catalog preparation is a real internal seam owned by the `EffectRuntime`
module; it is not a second registry module. `TurnExecution` uses it after core,
principal, revision, capability, and workspace bindings are fixed:

```text
prepare_catalog(TurnExecutionContext) -> PreparedEffectCatalog
PreparedEffectCatalog.definitions
PreparedEffectCatalog.resolve(provider_tool_name) -> ResolvedEffectRef
PreparedEffectCatalog.close()
```

`prepare_catalog()` applies namespace and connect policy, performs approved MCP
connect/discovery, and freezes definitions plus opaque resolved references.
`ContextManager.prepare()` receives those final definitions before provider IO.
The model loop resolves a returned tool name only through that catalog and
passes its opaque reference to `execute()`. Catalog connections/resources close
from `TurnExecution`'s `finally` path. These operations are internal Host
composition and are not exposed to Agent Core authors.

`EffectResult` distinguishes at least:

```text
succeeded | denied | invalid | not_found | failed | timed_out | cancelled | indeterminate
```

It records whether execution started and provides independently bounded,
redacted model, operator, event, and durable views. Raw adapter output remains
internal.

### Ordering and Invariants

Every builtin, authored, and MCP invocation follows one order:

1. Validate the request and resolved catalog binding.
2. Enforce principal/tool visibility and owner scope.
3. Require the capability snapshot.
4. Run pure Host checks: namespace, workspace/cwd, command, URL/redirect,
   process, environment, and output policy.
5. Resolve approval.
6. Bind only explicitly authorized secrets/environment.
7. Invoke the selected adapter under deadline and cancellation.
8. Complete cleanup, streaming limits, redaction, safe views, and audit state.

For a Host-mediated model-triggered effect, no authored tool import/invocation,
subprocess spawn, MCP connect/discovery, file mutation, or network effect may
occur before the applicable capability and approval checks. This statement does
not claim control over arbitrary Python/OS calls made directly by already
imported `host_shared` Slot code; `SlotRuntime` and optional isolation own that
separate risk.

MCP connection/discovery is its own `mcp.connect:<server>` effect. A later
`mcp.call:<server>` uses the exact connection-bound resolved entry; a global
tool-name index is never the dispatch authority.

An explicit cancellation request returns `cancelled` only after cleanup is
confirmed; uncertain process-tree or remote cleanup returns `indeterminate`.
If the host coroutine itself is cancelled, the module first persists the same
typed outcome and then re-raises cancellation instead of converting it to an
ordinary tool error. A crash after an external side effect but before durable
confirmation is also `indeterminate`. Non-idempotent foreground effects are not
replayed automatically after restart. Durable/background effects return a
Host-work handle and use that subsystem's recovery contract.

Output is bounded while reading or streaming, not after loading the full file,
tree, subprocess output, MCP result, or event payload. Discovery has per-server
timeouts, bounded parallelism, failure backoff, and lifecycle eviction.

`host_shared` does not become a sandbox merely because invocation policy is
centralized. Once imported, authored Python can use ordinary Python/OS APIs.
Optional subprocess/per-core isolation is a later adapter at the same seam.

## ContextManager

### Interface

```text
await ContextManager.prepare(ContextRequest) -> PreparedContext
await ContextManager.observe(UsageObservation) -> None
```

`ContextRequest` contains:

- immutable `TurnExecutionContext`;
- step id and normalized immutable model limits supplied by `ProviderRuntime`;
- frozen current-turn messages and context contributions;
- final per-turn effect definitions, so schema overhead is budgeted;
- bootstrap-use flag.

`ProviderRuntime` owns provider/profile normalization such as context window,
maximum output, tokenizer/estimator identity, and provider safety margins.
`ContextManager` alone chooses the per-step input/output budget from those
limits; callers do not pass a precomputed reservation.

History, bootstrap snapshot, summary/cutoff, leases, estimators, persistence,
and summarizer clients are implementation knowledge.

`PreparedContext` is a tagged `ready | overflow` result. A ready result returns
provider-neutral immutable messages, chosen output budget, estimated input size
and hard budget, an opaque decision id, and a bounded non-sensitive
decision/layer summary. An overflow result contains no provider request and
gives a typed, bounded recovery reason.

`UsageObservation` is correlated by decision id plus session/turn/step,
provider/model, input/output/cache token buckets, finish reason, and provider
request id. `observe()` is idempotent and never updates an ambient global "last
usage" record.

`TurnExecution` calls `observe()` after response normalization and before the
next `prepare()` or terminal commit. The observation is durably appended before
calibration state advances. A typed observation-write failure never causes the
provider request to be repeated or its valid response to be discarded: the turn
records degraded context telemetry, skips the uncommitted calibration, and the
next `prepare()` uses conservative estimates or returns `overflow` if safety
cannot be established.

### Ordering and Invariants

`prepare()`:

1. Reads history through `TurnExecutionContext.session_id`.
2. Chooses the output reservation and computes the input budget from normalized
   model limits, schema overhead, and safety margin.
3. Assembles layers deterministically.
4. Cheap-prunes old tool/media results before summarization.
5. Estimates the complete provider request.
6. If needed, acquires a session compaction lease, revalidates the snapshot,
   compacts, and atomically commits summary plus cutoff.
7. Preserves current input, causal assistant/tool groups, protected head/tail,
   and reference-only summary semantics.
8. Returns bounded context or a typed overflow result before provider I/O.

Summary failure uses deterministic bounded fallback or preserves the original
context when it still fits. Cancellation commits no partial summary/cutoff and
releases the lease. Lease/cooldown state is restart-recoverable. A model switch
invalidates prior calibration. Normal events never persist the full prompt;
explicit debug output is bounded and redacted.

If another worker owns the compaction lease, `prepare()` waits only for a
bounded interval, then rereads the committed summary/cutoff. If no usable commit
appears, it applies deterministic cheap fallback or returns `overflow`; it does
not start a second summarizer or wait indefinitely.

`prepare()` works from a bounded retained window rather than repeatedly loading
the full transcript. `observe()` is effectively O(1).

The current `ContextAssembler` controls layer order, while
`PromptContextRuntime` reads mutable runner session state and
`SessionCompactionRuntime` owns a separate manual flow. Those are internal
implementation pieces to fold behind this interface, not additional external
owners.

## ChannelInbox

### Interface and Vocabulary

```text
ChannelInbox.accept(InboundEnvelope) -> InboxReceipt
ChannelInbox.claim() -> ClaimedInbound | None
ChannelInbox.complete(ClaimedInbound, InboundResult) -> CompletionDecision
ChannelInbox.fail(ClaimedInbound, InboundFailure) -> RetryDecision
```

`InboundEnvelope` contains a channel-instance id, stable platform event key,
kind, canonical conversation key, authenticated principal facts, received
time, bounded payload/payload reference, artifact references, and optional
source checkpoint. Platform adapters provide facts; they never construct
operator/admin `PrincipalScope`.

`InboxReceipt` returns a durable inbound id and
`accepted | duplicate` disposition. A duplicate returns the same durable
identity.

`ClaimedInbound` includes the envelope, claim token, attempt, lease expiry, and
a stable turn-request/idempotency id reserved from the durable inbound id.
Existing turn/result correlation is included when reconciliation finds it.
`InboundResult` supports turn completion, command completion, ignored input, and
cancellation; not every inbound creates a model turn. `InboundFailure` is typed
and redacted.

### Ordering and Invariants

1. Body limits, signature/token, allowlist, and minimum parsing run before
   `accept()`.
2. For one event, durable envelope and dedup identity commit atomically. An
   internal batch/checkpoint operation commits every new envelope, dedup key,
   and the source checkpoint in one transaction; the public `accept()` is its
   single-envelope equivalent.
3. Transport acknowledgement happens only after `accepted` or `duplicate`:
   push returns 2xx/202, Email marks `Seen`, and polling may advance its cursor.
4. Store failure returns 5xx or retains the old cursor/unread message.
5. `claim()` uses a lease/token; only the current claimant can complete/fail.
6. Before starting a model turn, the worker durably reserves the stable
   request/idempotency id. Crash before claim is recoverable; crash during a turn
   reconciles that id and any existing turn before creating another turn.
7. User-requested turn cancellation is terminal and does not replay the
   original message.
8. Worker shutdown without a business result releases/expires the lease for a
   transient retry.
9. Attempts and payloads are bounded. Authentication, signature, allowlist,
   parse, and body-limit failures happen before `accept()` and return transport
   4xx/413 without creating inbox/DLQ rows. Authenticated but unprocessable or
   repeatedly failing poison events become terminal reject/dead-letter records.
10. Inbox completion does not mean outbound delivery succeeded; the outbox and
    `DeliveryRuntime` retain that ownership.

Retry uses bounded exponential backoff plus jitter and ends in a redacted DLQ.
Operator requeue is explicit; there is no infinite automatic replay.

`complete()` and `fail()` are idempotent for the same claim token and terminal
payload. `CompletionDecision` and `RetryDecision` include typed
`stale_claim | already_terminal | conflict` dispositions in addition to their
normal complete/retry/dead-letter outcomes; they never overwrite the current
claimant or silently change a terminal record.

Dedup uses an indexed unique `(channel_instance_id, platform_event_key)` key.
Due claims use indexed next-attempt/lease fields, bounded claim batches, and
fairness across channel instances rather than full-table scans or one noisy
channel monopolizing workers. Payload/artifact retention is bounded separately
from dedup tombstones. The dedup replay horizon is at least the maximum supported
transport retry/replay window; pruning payloads never permits an old platform
event to be accepted again inside that horizon, and active/DLQ evidence is not
pruned while still actionable.

Durable stream checkpoints are an internal `ChannelInbox` seam. A batch cursor
advances only after every event in the batch is durably accepted; replay is
absorbed by the unique dedup key.

The production adapter is SQLite-backed. A strict in-memory adapter runs the
same claim/lease/idempotency contract suite. Platform transports remain
protocol adapters, not alternative inbox owners.

## External and Internal Seams

| Concern | External Host seam | Internal implementation seams/adapters |
| --- | --- | --- |
| Turn lifecycle | `TurnExecution` | admission, persistence, provider loop, slot, IO, delivery, and test hosts bound to one context |
| Authority | `PrincipalScope` on owner interfaces | authenticated channel/operator/system/delegated-agent resolvers |
| Effects | `EffectRuntime` | catalog prepare/resolve/close, builtin/authored/MCP adapters, approval provider, process executor, URL policy, secret redactor, output views |
| Context | `ContextManager` | history store, estimator, compaction lease, summarizer, fallback, telemetry |
| Inbound channels | `ChannelInbox` | SQLite/in-memory inbox, source checkpoint store, platform envelope adapters |

Production and test adapters justify the internal seams. Do not expose a
test-only adapter to Agent Core authors, and do not create hypothetical public
ports for dependencies that do not yet vary.

## Primary Finding Owners

Every audit finding has one primary owner. Other modules may supply internal
helpers, but they do not become a second policy owner. Finding IDs are
contributor/regression labels rather than public runtime identifiers; use the ID
to locate its probe or permanent test and implementation history.

| Primary owner module | Findings |
| --- | --- |
| `TurnExecution` | SES-01 |
| `EffectRuntime` | SEC-01, TOOL-01, TOOL-03, ENV-01, MCP-01, MCP-02, MCP-03, PROC-01, NET-01, IO-01, TOOL-02 |
| `ApprovalRuntime` | AUTH-01 |
| `SessionRuntime` | SES-02 |
| `StateRuntime` | STATE-01 |
| `RuntimeStore` | STORE-01 |
| `RuntimeControlPlane` | TASK-01 |
| `RuntimeTaskWorker` | TASK-02, TASK-03, LOG-01 |
| `SchedulerRuntime` | SCHED-01 |
| `ProviderRuntime` | PROV-01, PROV-02 |
| `ContextManager` | CTX-01 |
| `SlotRuntime` | SLOT-01, MOD-01 |
| `ChannelInbox` | CH-01, CH-02, CH-03 |
| `ChannelSupervisor` | CH-04 |
| `DiagnosticsRuntime` | CLI-01, CLI-02, CLI-03, SETUP-01 |
| `TuiLauncher` | TUI-01 |
| `OperatorGatewayRuntime` | UI-01 |
| `OperatorTui` | UI-02 |
| `ManagedUpdateRuntime` | UPDATE-01 |
| Webhook transport adapter | HTTP-01 |
| `RuntimeSecurityPolicy` | SEC-02 |

## Migration and Deletion Rules

- New contracts replace old shallow forwarding paths; they do not add a
  permanent second route around them.
- `Runner*Host` adapters may remain internal only when bound to one immutable
  execution context. A generic runner back-reference is not the external seam.
- Registry definitions, operator display, provider schemas, and execution use
  the same resolved effect entry.
- Current `InteractionInbound` becomes a compatibility DTO between inbox worker
  and TurnExecution, not the durable inbox schema.
- The JSON-backed `StateStore` is containment only. Final production state
  semantics belong to `StateRuntime`, implemented on the transactional
  `RuntimeStore`; there is no permanent JSON/SQLite dual owner. The containment
  serializes a resolved state path within one process, uses content-hash CAS for
  internal stale-writer detection, and publishes state plus proposal audit with
  atomic files and a prepared/committed recovery journal. POSIX state paths use
  explicit private mode bits; Windows follows platform ACL semantics. The
  containment deliberately does not claim cross-process locking or make separate
  authored `get()` and `set()` calls transactional. Existing JSON documents
  require no schema rewrite; migration later imports them into `StateRuntime` and
  retires the JSON writer.
- Existing `DeliveryRuntime`, `SessionRuntime`, `RuntimeStore`, task worker,
  scheduler, provider, and slot modules keep their specialized ownership unless
  the owner table explicitly moves it.
- Breaking cleanup may delete private forwarding methods and old internal
  layout. Compatibility shims require an explicit migration decision.

## Reference-Project Limits

Hermes is a read-only mechanism reference, not a target architecture or code
source. Useful ideas include admission-before-await, process-tree lifecycle,
context budgeting, retry vocabulary, and cursor/dedup test scenarios.

Do not copy its gateway god-file, public context plugin engine, large regex
policy as a sandbox, runtime lazy dependency installation, or broad adapter
compatibility surface. No Hermes code is copied by this contract.
