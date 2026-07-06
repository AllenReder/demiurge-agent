# Operator Gateway Product Surface Plan

## Priority

P4. This should follow the runtime lifecycle work because the operator UI needs
stable runtime projections before its product surface can mature.

## Current Problem

`TuiInteractionBridge` is doing too many jobs:

- local TUI gateway adapter;
- slash command router;
- interaction route implementation;
- prompt and approval state owner;
- status projection builder;
- scheduler starter/stopper;
- session history emitter;
- busy/interrupt queue surface;
- package/core/session/task command dispatcher.

The TypeScript TUI is mostly rendering and RPC plumbing, while the Python bridge
has become both adapter and product gateway. This blocks a Hermes-level
dashboard/TUI surface because every new operator feature risks becoming another
method on `TuiInteractionBridge`.

## Hermes Reference Point

Hermes has a more mature operator gateway product shape:

- `tui_gateway/server.py` has session registries, crash logging, RPC long-handler
  isolation, session resume/close/finalize hooks, orphan session reaping, and
  transport detachment behavior.
- `gateway/session.py` models source/session context explicitly, including
  platform/chat/user/thread metadata and route context.
- Hermes keeps platform messaging adapters under `gateway/platforms/*` and a
  separate `tui_gateway`; operator UI is not treated as just another messaging
  channel.

Demiurge should absorb the gateway/product lessons while preserving its cleaner
host/authored split.

## Modification Plan

Add `OperatorGatewayRuntime` as the Python-side product gateway for TUI and
future dashboard clients.

### Responsibilities

- own operator event stream projection (`operator.*` events);
- expose session, status, work, prompt, approval, history, and command views;
- own prompt/approval pending state in a reusable module;
- expose stable status snapshots for TUI/dashboard;
- connect to `InteractionRuntime`, `ConversationLifecycleRuntime`, and future
  `HostWorkLifecycleRuntime`;
- isolate long-running operator commands where needed so approval/interrupt can
  stay responsive.

### TUI Adapter Shape

`TuiInteractionBridge` should become a narrow adapter:

- translate TUI RPC requests into `OperatorGatewayRuntime` commands;
- translate `OperatorGatewayRuntime` events into JSON protocol frames;
- keep terminal-specific rendering preferences such as tool display level;
- avoid owning package/session/task/scheduler product logic directly.

### Protocol Direction

The TypeScript TUI should consume stable product events rather than bridge-local
implementation events where practical:

- `operator.ready`;
- `operator.status`;
- `operator.history`;
- `operator.work.updated`;
- `operator.prompt.opened`;
- `operator.approval.opened`;
- `operator.error`;
- `interaction.*` remains for actual assistant/user transcript delivery.

### Boundary With Channels

Operator UI and messaging channels share
`InteractionInbound` / `InteractionOutbound`, but they are not the same adapter
kind:

- channels own external platform concerns such as allowlists, reply/thread
  routing, remote delivery, and `run_forever()`;
- operator gateway owns local control-plane concerns such as sessions, tasks,
  packages, schedules, approvals, status, history, and runtime observability.

Do not model TUI/dashboard as `Channel` just to reuse names.

## Expected Advantages

- TUI and future dashboard can share one operator gateway instead of duplicating
  bridge logic.
- Session/status/work/prompt/approval state becomes a product surface, not a
  side effect of one terminal adapter.
- Hermes-style reliability features can be added incrementally: crash evidence,
  long-command isolation, orphan session cleanup, work watcher summaries.
- Messaging channels remain focused on platform adaptation and do not inherit
  operator-control concerns.

## Validation

Run Python gateway and TUI checks:

```bash
uv run pytest tests/channels/test_ui_gateway.py tests/runtime/test_outbound_delivery.py tests/runtime/test_conversation_lifecycle.py
cd ui-tui && npm test -- --run
cd ui-tui && npm run typecheck
cd ui-tui && npm run build
cmp ui-tui/dist/entry.js demiurge/ui/tui_dist/entry.js
git diff --check -- demiurge/ui_gateway demiurge/runtime ui-tui goal/now
```

If `npm run build` changes `ui-tui/dist/entry.js`, copy the packaged bundle only
through the repo's established TUI packaging flow and rerun the `cmp` contract.

## Scope Boundaries

Do not add dashboard-only dependencies. Do not move messaging channels into this
operator module. Do not change Agent Core, Agent Slot, or Package capability
concepts.
