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
- `RuntimeControlPlane`: host-owned action and task seam.
- `SessionRuntime`: session admission and session/turn/message projections.
- `TurnEngine`: one `agent.turn` task's provider/tool loop.
- `SlotRuntime`: phase-specific authored slot callable execution.

The control-plane model is:

```text
ActionSpec -> Task -> Event -> Projection
```

Every turn, subagent, terminal command, evolver run, scheduled fire, delivery,
approval, MCP call, authored tool call, state patch, and artifact write should
enter through `RuntimeControlPlane`.

## Storage

The runtime database is `~/.demiurge/runtime/runtime.sqlite3`. It uses Python
stdlib `sqlite3` with WAL mode. Old JSON/JSONL session, scheduler, and
background-task state is not migrated. In-progress subprocess work found after
restart must be marked `lost` or `interrupted`; the host must not replay dangerous
effects after a crash.

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
messages, task status, task logs, scheduler instances, artifacts, and delivery
outbox rows. Old JSON session and scheduler files may still exist on disk from
older installs, but runtime code does not read, migrate, or dual-write them.

`RuntimeTaskWorker` is the live worker for active subprocess, terminal,
evolver, and child-agent work. It keeps only non-durable process handles,
cancel callbacks, and live completion subscribers in memory. Public task reads,
lists, logs, waits, cancellation results, and pending completion notifications
are rebuilt from `RuntimeControlPlane` / SQLite projections and runtime events.

`DeliveryRuntime` dispatches queued delivery intents through channel bridges and
updates the SQLite outbox projection with `sent` or `failed` status. Delivery
failure can update a previously persisted history row with explicit failure
history text, but retries must not rewrite the original history body.

`SessionTurnStepRunner` now delegates:

- session creation, update, turn lifecycle, and message persistence to
  `SessionRuntime`;
- provider/tool loop execution to `TurnEngine`;
- authored bootstrap/input/output slot callable loading and invocation to
  `SlotRuntime`.

The model-facing delegation tools are:

- `delegate_task(goal, core_id=None, context_mode="isolated",
  notify_policy="return_to_parent", tool_policy=None, max_depth=None)`;
- `task_list(kind=None)`, scoped to the current session;
- `task_status(task_id, view="model")`;
- `task_control(task_id, command="cancel")`;
- `yield_until(task_id, timeout_seconds=30)`.

`delegate_task` currently supports `isolated` and `fork` context modes, enforces
the default depth and child-count limits, and applies child `tool_policy`
filters during visible-tool construction and dispatch. `notify_policy` accepts
only `return_to_parent` and `silent`; the former emits a completion event and
the latter suppresses it. Child output is evidence for the parent by default.
