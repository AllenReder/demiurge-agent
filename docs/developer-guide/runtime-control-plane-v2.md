---
title: Runtime Control Plane v2
description: Host-owned action, task, event, projection, and Agent Slot v2 design.
---

# Runtime Control Plane v2

This document records the implementation contract for the breaking runtime
refactor. The host owns the harness. Agent Cores own authored files under
`agent/`.

## Module Shape

The new deep Modules are:

- `RuntimeStore`: SQLite event store and projection surface.
- `RuntimeControlPlane`: host-owned detached task seam.
- `DurableWorkRuntime`: lease/ack/terminal-state seam for unfinished host work.
- `HostWorkLifecycleRuntime`: unified lifecycle and observation seam for
  durable work, detached tasks, delivery, schedule fires, and task-completion
  notifications.
- `SessionRuntime`: session admission and session/turn/message projections.
- `TurnEngine`: foreground provider/tool loop for one Agent Core turn.
- `SlotRuntime`: phase-specific authored slot callable execution.

The detached-work task ledger model is:

```text
TaskSpec -> Task -> Event -> Projection
```

Detached host work that is observable as a task enters through
`RuntimeControlPlane.submit_task()`. The current task-ledger kinds are
`agent.spawn`, `terminal.exec`, `evolver.run`, and `schedule.fire`. Foreground
Agent Core turns do not become task rows; they are projected through
`SessionRuntime` as turns and messages. Delivery, approval, tool-call, MCP,
state, and artifact facts should use their own projections or runtime events
rather than fake task submissions.

## Storage

The runtime database is `~/.demiurge/runtime/runtime.sqlite3`. It uses Python
stdlib `sqlite3` with WAL mode. Old JSON/JSONL session, scheduler, and
background-task state is not migrated.

Unfinished host work is projected into `runtime_work_items`. This table is the
durable lease surface for work that has been created but has not reached a
terminal state, including delivery sends, schedule fires, and background-task
completion notifications. Callers should use `DurableWorkRuntime` instead of
writing ad hoc `queued`, `running`, `sending`, `sent`, or `acknowledged` state.
Expired `running` or `claimed` work can be reclaimed by a new claim token.
Expired `sending` work is marked `unknown`; the host must not blindly replay an
external send after a crash.

Runtime modules that need a cross-subsystem view use
`HostWorkLifecycleRuntime`. It wraps `DurableWorkRuntime` behind domain
methods for delivery sends, schedule fires, and task-completion notifications,
and it projects operator-readable status from `runtime_work_items`, `tasks`,
`task_logs`, `outbox`, `scheduler_instances`, and task-completion events. This
is an observation and lifecycle facade, not a replacement for the specialized
owners:

- `RuntimeTaskWorker` still owns active process handles, live task objects,
  cancel callbacks, wait, and live completion subscribers.
- `DeliveryRuntime` still owns channel dispatch, item dispatch status, message
  failure updates, and delivery event-log records.
- `SchedulerRuntime` still owns cron calculation, due-instance records, and
  fresh scheduled sessions.
- `DurableWorkRuntime` still owns the low-level claim token state machine.

Foreground Agent Core turns are not host work items and must not be promoted
into task ids. Memory review, background review, curator behavior, and learning
loops are also not harness modules; those capabilities belong in Agent
Slot-driven packages using host-mediated tools and capabilities.

## Agent Slot Layout

Each bootstrap, input, and output slot owns a `slot.yaml` manifest in its slot
directory. `agent/pipelines.yaml` is the single phase ordering graph:

```yaml
schema_version: 1
bootstrap:
  serial: []
input:
  serial: [base_input]
  parallel: []
output:
  serial: [base_output]
  parallel: []
```

Slot code and metadata stay in typed folders:

```text
agent/bootstrap/<slot_id>/module.py
agent/bootstrap/<slot_id>/slot.yaml
agent/input/<slot_id>/module.py
agent/input/<slot_id>/slot.yaml
agent/output/<slot_id>/module.py
agent/output/<slot_id>/slot.yaml
```

`base_input` and `base_output` are ordinary editable seed slots. The host does
not treat them as built-ins and the loader does not require those ids.

Input slots build the current model context. `ctx.input.raw_text` is read-only.
Slots use `ctx.input.add_context(text, role="user"|"system",
write_history=...)`. Output slots read `ctx.output.response_text` and use
`ctx.output.send_*`. The author-facing delivery timing parameter is removed:
every send records a delivery intent immediately.

Serial slots can affect the main flow. Parallel slots are non-blocking
background side-effect lanes and cannot modify prompt, assistant response, or
session history.

## Current Implementation Slice

The runtime store is now the hot-path source of truth for sessions, turns,
messages, foreground tool-call records, task status, task logs, scheduler
instances, artifacts, delivery outbox rows, runtime work items, and unique
channel conversation bindings. Foreground tool-call records are keyed by the
current `turn_id` and model-loop `step_id`; they are not task facts. Old JSON
session and scheduler files may still exist on disk from older installs, but
runtime code does not read, migrate, or dual-write them.

`RuntimeTaskWorker` is the live worker for active subprocess, terminal,
evolver, and child-agent work. It keeps only non-durable process handles,
cancel callbacks, and live completion subscribers in memory. Public task reads,
lists, logs, waits, cancellation results, and pending completion notifications
are rebuilt from `RuntimeControlPlane` / SQLite projections and runtime events.
Task-completion claim and acknowledgement flow through
`HostWorkLifecycleRuntime`, so bridges and operator surfaces share the same
claim/ack vocabulary.

`BackgroundWorkRuntime` tracks in-process background coroutines created by
parallel slots and delivery dispatch. It composes those local tasks with the
durable `RuntimeTaskWorker` for drain and active-count behavior; the foreground
runner does not own a separate background-task ledger.

`OperatorGatewayRuntime` owns the local operator product surface for TUI and
future dashboard clients. It projects `operator.ready`, `operator.status`,
`operator.history`, `operator.work.updated`, `operator.prompt.opened`,
`operator.approval.opened`, `operator.message`, `operator.deliver`,
`operator.error`, and `operator.shutdown` events from the runtime store, session
runtime, conversation lifecycle, approval runtime, and
`HostWorkLifecycleRuntime`. The NDJSON entrypoint is only transport plumbing
over this operator module; messaging channels remain separate platform
adapters. Operator clients do not receive legacy `interaction.*` or
`channel.*` compatibility frames.

`DeliveryRuntime` dispatches queued delivery intents through the
session-scoped interaction router after claiming the matching durable work item.
The outbox lifecycle is `queued -> sending -> sent/failed/unknown/unrouted`.
`unrouted` means no live route is bound for the delivery session; `failed` means
a route existed and adapter delivery raised. Delivery failure can update a
previously persisted history row with explicit failure history text, but retries
must not rewrite the original history body.

`SessionTurnStepRunner` now delegates:

- session creation, update, turn lifecycle, and message persistence to
  `SessionRuntime`;
- foreground turn admission, including session/core resolution, route binding,
  revision/capability pinning, and turn begin, to `TurnAdmissionRuntime`;
- authored input -> model/tool -> output execution, captured-route context,
  bootstrap, owner-checked cancellation, delivery drain, and final cleanup to
  `TurnExecution`;
- foreground input records, assistant output records, display turns,
  completion, and interruption to `TurnPersistenceRuntime`;
- provider/tool loop execution to `TurnEngine`;
- authored bootstrap/input/output slot callable loading and invocation to
  `SlotRuntime`.

The model-facing delegation tools are:

- `delegate_task(goal, core_id=None, context_mode="isolated",
  notify_policy="return_to_parent", max_depth=None, tools="all",
  input_slots=["base_input"], output_slots=["base_output"],
  use_bootstrap=False)`;
- `task_list(kind=None)`, scoped to the current session;
- `task_status(task_id, view="model")`;
- `task_control(task_id, command="cancel")`;
- `yield_until(task_id, timeout_seconds=30)`.

`delegate_task` currently supports `isolated` and `fork` context modes, enforces
the default depth and child-count limits, and applies child `tools` selection
during visible-tool construction and dispatch. `notify_policy` accepts only
`return_to_parent` and `silent`; the former emits a completion event and the
latter suppresses it. Child output is evidence for the parent by default.
Child input/output selection defaults to `base_input` and `base_output`;
`"all"` runs the child core's full configured pipeline, and a list filters the
active pipeline while preserving order and serial/parallel groups. Bootstrap is
off by default for delegated child turns.

Foreground turns are not readable as task ids through the control-plane
projection. They remain traceable through the session, turn, message, event-log,
and runtime-event projections. Model-facing task tools only operate on detached
background task kinds, so ordinary turns neither appear in `task_list` nor
support `task_status`, `task_control`, or `yield_until`.
