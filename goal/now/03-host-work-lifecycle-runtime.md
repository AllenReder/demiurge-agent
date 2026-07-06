# Host Work Lifecycle Runtime Plan

## Priority

P3. This should run after the turn pipeline split because it unifies the
lifecycle language used by detached work and cross-turn host work.

## Current Problem

Demiurge has converged on durable host work concepts, but the interfaces remain
scattered:

- `RuntimeTaskWorker` owns active process handles, task logs, wait/cancel, and
  task completion notifications.
- `DurableWorkRuntime` owns claim, lease, terminal state, acknowledge, and
  recovery for `runtime_work_items`.
- `DeliveryRuntime` uses durable work for outbox sends.
- `SchedulerRuntime` uses durable work for schedule fires.
- `ChildAgentRuntime` and delegation tools create task records and completion
  events.
- `CompletionInbox` claims pending completion events and turns them into
  inbound background-completion messages.

Each subsystem has useful local behavior, but there is no single host lifecycle
module that names and observes work consistently across task, delivery,
schedule, child-agent, and completion delivery. The result is repeated
claim/status/completion vocabulary at multiple seams.

## Hermes Reference Point

Hermes `tools/process_registry.py` is valuable here because it models managed
work as a first-class runtime concern:

- status polling;
- output/log buffers;
- blocking wait;
- kill/cancel;
- notification queues;
- watch-pattern throttling;
- checkpoint/recovery;
- session-scoped metadata for gateway reset protection.

The lesson is not memory or background skill review. Those are explicitly out
of scope for Demiurge's harness and should remain Agent Slot + Package
capabilities. The lesson is that host-managed work needs a durable observation
and lifecycle vocabulary that product surfaces can trust.

## Modification Plan

Add `HostWorkLifecycleRuntime` as an observation and lifecycle facade over the
existing durable/task modules.

### Responsibilities

- provide a unified status view for durable work items and task records;
- expose lifecycle verbs with consistent naming:
  `claim`, `status`, `complete`, `fail`, `cancel`, `acknowledge`,
  `list_events`;
- adapt task logs and completion events into operator-readable work events;
- expose session-scoped work summaries for TUI/dashboard/status surfaces;
- keep delivery, schedule, task completion, and child-agent work under the same
  vocabulary without flattening them into one storage table.

### Non-Responsibilities

- do not replace `DurableWorkRuntime`;
- do not replace `RuntimeTaskWorker` process-handle ownership;
- do not make foreground turns task ids;
- do not implement Hermes background review, curator, or memory review in the
  harness;
- do not add new external dependencies.

### Integration Direction

- `DeliveryRuntime` should report through the lifecycle facade when claiming,
  sending, sent, failed, unknown, or unrouted.
- `SchedulerRuntime` should use the facade vocabulary for schedule fire claims
  and completion records.
- `RuntimeTaskWorker` should remain the live worker, but its model-facing
  task-list/status/control paths should align with the facade's status names.
- `CompletionInbox` should keep session-scoped claim/ack behavior, but the
  completion events should be visible through `HostWorkLifecycleRuntime`.

## Expected Advantages

- TUI/dashboard can show one coherent "host work" view instead of separately
  querying tasks, outbox, schedule state, and pending completions.
- Delivery, schedule, task, and child-agent flows share status language while
  keeping their specialized storage and execution owners.
- Future process watcher behavior can be added to host work observation without
  coupling it directly to channels or TUI.
- Package-owned Agent Slot features can still create behavior on top of host
  capabilities without becoming harness code.

## Validation

Run the lifecycle-heavy tests:

```bash
uv run pytest tests/runtime/test_durable_work.py tests/runtime/test_tasks.py tests/runtime/test_outbox.py tests/runtime/test_delegation_tools.py tests/runtime/test_completions.py tests/runtime/test_ingress.py
uv run pytest tests/scheduler tests/channels/test_ui_gateway.py
uv run python -m compileall demiurge/runtime demiurge/scheduler tests/runtime tests/scheduler
git diff --check -- demiurge/runtime demiurge/scheduler tests goal/now
```

If scheduler tests are not under `tests/scheduler`, locate the current scheduler
test files with `rg -n "SchedulerRuntime|schedule.fire|claim_due" tests` and run
that focused set.

## Scope Boundaries

Memory, background review, skill curator, and learning-loop review are excluded
from this harness plan. They belong in installable Package capability packs
implemented through Agent Slots and host-mediated tools/capabilities.
