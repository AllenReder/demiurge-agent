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
- `SessionRuntime`: admission, session projections, busy queues, and completion
  merge.
- `TurnEngine`: one `agent.turn` task, including provider/tool loop and slot
  invocation.
- `SlotRuntime`: phase-specific authored slot execution.

The control-plane model is:

```text
ActionSpec -> Task -> Event -> Projection
```

Every turn, subagent, terminal process, evolver run, scheduled fire, delivery,
approval, MCP call, authored tool call, state patch, and artifact write should
enter through `RuntimeControlPlane`.

## Storage

The runtime database is `~/.demiurge/runtime/runtime.sqlite3`. It uses Python
stdlib `sqlite3` with WAL mode. Old JSON/JSONL session, scheduler, and job state
is not migrated. In-progress local process/subprocess work found after restart
must be marked `lost` or `interrupted`; the host must not replay dangerous
effects after a crash.

## Agent Slot v2

`agent/slots.yaml` is the single declaration and pipeline graph for bootstrap,
input, and output slots:

```yaml
version: 2
slots:
  bootstrap: {}
  input:
    base_input:
      failure: hard
      capabilities: []
  output:
    base_output:
      failure: soft
      capabilities: []
pipelines:
  bootstrap:
    serial: []
  input:
    serial: [base_input]
    parallel: []
  output:
    serial: [base_output]
    parallel: []
```

Slot code stays in typed folders:

```text
agent/bootstrap/<slot_id>/module.py
agent/input/<slot_id>/module.py
agent/output/<slot_id>/module.py
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
