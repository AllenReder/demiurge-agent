# Interaction Runtime Orchestration Plan

## Priority

P1. This should be the next implementation loop because it removes the most
visible shallow orchestration from the live interaction path while keeping the
change contained.

## Current Problem

`InteractionRuntime.handle()` is still a mixed adapter/turn/finalization seam.
It currently:

- binds the inbound route through `runner.run_turn(...)`;
- drains local background work after the foreground turn;
- extracts `UserPromptRequest` from tool result payloads;
- filters pending `InteractionItem` values;
- packages the final `InteractionOutbound`.

That makes `InteractionRuntime` look like a small facade, but it still knows
too much about turn execution, background drain policy, tool-result prompt
encoding, and outbound projection. The module is therefore shallow: callers get
one entrypoint, but the hidden cost is that unrelated policies have to change in
one method.

## Hermes Reference Point

Hermes still has a large agent loop, but the useful lesson is its direction of
decomposition:

- `agent/conversation_loop.py` owns the main model/tool loop.
- `agent/turn_finalizer.py` owns post-loop finalization and result assembly.
- Gateway code invokes the agent and routes the result; it does not parse every
  finalization detail inline.

Demiurge should not copy Hermes code or recreate its monolithic agent object.
The useful idea is to separate execution from response projection so the live
gateway path stays thin and product adapters do not become turn-finalizers.

## Modification Plan

Create two deep runtime modules below the current interaction facade.

### `InteractionExecutionRuntime`

Responsibilities:

- normalize `route` into `SessionRouteBinding` when needed;
- call `runner.run_turn(...)` with `InteractionInbound`;
- drain `runner.background_tasks.drain(include_runtime_tasks=False)` after the
  foreground turn;
- return the raw `TurnResult`.

Non-responsibilities:

- no prompt extraction;
- no item filtering;
- no outbound construction;
- no channel/TUI formatting.

### `InteractionResponseRuntime`

Responsibilities:

- build `InteractionOutbound` from `TurnResult` and `InteractionInbound`;
- filter `result.items` to pending dispatch items;
- extract `UserPromptRequest` from `result.needs_user` tool-result payloads;
- preserve inbound metadata (`source`, `reply_to`, `conversation_key`, channel
  metadata) in the outbound.

Non-responsibilities:

- no turn execution;
- no background draining;
- no route binding;
- no adapter delivery.

### Facade Shape

`InteractionRuntime.handle()` should become a thin orchestrator:

```text
execution_result = await execution.run(inbound, route_binding=...)
return response.build(execution_result, inbound)
```

Delete the private `_prompt_from_tool_results()` method from
`InteractionRuntime`; if a helper remains, it belongs in
`InteractionResponseRuntime`.

## Expected Advantages

- Interaction execution policy and response projection become independently
  testable.
- Gateway, TUI, and channel adapters get a stable narrow entrypoint without
  inheriting prompt extraction or drain details.
- Future operator gateway work can reuse the response projector without running
  turns.
- The runner remains the foreground turn owner, but `InteractionRuntime` no
  longer acts as an implicit turn finalizer.

## Validation

Run the focused runtime and adapter tests:

```bash
uv run pytest tests/runtime/test_interactions.py tests/runtime/test_outbound_delivery.py tests/channels/test_ui_gateway.py
uv run python -m compileall demiurge/runtime tests/runtime tests/channels
git diff --check -- demiurge/runtime tests/runtime tests/channels goal/now
```

## Scope Boundaries

Do not change Agent Core, Agent Slot, or Package concepts. Do not preserve old
private helper names. Do not add dependencies.
