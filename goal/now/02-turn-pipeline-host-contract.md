# Turn Pipeline Host Contract Plan

## Priority

P2. This follows the interaction cleanup because it deepens the foreground turn
pipeline itself and will likely touch more runtime tests.

## Current Problem

`TurnPipelineRuntime` has the right high-level responsibility, but its host
interface is still too broad. `TurnPipelineHost` exposes runner details across
many unrelated concerns:

- core loading and active-session core updates;
- session resolution and route binding;
- session-start event emission;
- bootstrap execution;
- turn lifecycle begin/interrupt/complete;
- input and output slot execution;
- tool preparation and provider/tool loop execution;
- history refresh and display-turn append;
- runtime-error sanitization.

This makes the pipeline look modular while the interface remains shallow. The
pipeline needs to know the order of every runner-side concern, and
`RunnerTurnPipelineHost` becomes a passthrough table for runner internals.

## Hermes Reference Point

Hermes still has a large `conversation_loop.py`, so it is not a structural
template to copy. The useful references are the seams around the loop:

- `agent/turn_context.py` builds turn context instead of mixing it into every
  caller.
- `agent/turn_finalizer.py` centralizes persistence, cleanup, diagnostics, and
  result assembly after the main loop.

Demiurge should apply the same separation more cleanly: admission, authored turn
execution, and persistence/finalization should be separate host modules.

## Modification Plan

Split the current `TurnPipelineHost` into deeper host-owned modules.

### `TurnAdmissionRuntime`

Responsibilities:

- load or resolve the active `LoadedCore`;
- resolve session/core binding from `InteractionInbound` metadata;
- bind the session route;
- update active session core when the request uses the active core;
- emit `session.started` once per session;
- run bootstrap when requested;
- begin the turn lifecycle;
- return a `TurnExecutionScope`.

`TurnExecutionScope` should contain the already-resolved facts the authored
pipeline needs:

- `session_id`;
- `core`;
- `core_revision`;
- `capability`;
- `lifecycle`;
- `turn`;
- `interaction_metadata`;
- `state_stores`;
- `input_envelope`;

### `TurnPersistenceRuntime`

Responsibilities:

- record received/persisted user messages;
- append visible assistant deliveries to the turn message list;
- refresh session history;
- append `display_turns`;
- complete or interrupt lifecycle records;
- sanitize runtime errors.

### `TurnPipelineRuntime`

After the split, this runtime should own only the authored foreground turn:

```text
Input slots -> tool preparation -> TurnEngine -> Output slots -> TurnResult
```

The pipeline should consume a `TurnExecutionScope`, not a broad runner-shaped
host. It may call small injected interfaces for slot runtime, tool runtime, and
turn engine, but it should not directly know session admission or persistence
details.

### Deletions And Compression

- Delete or shrink `TurnPipelineHost` so it no longer lists every runner method.
- Delete broad passthrough methods from `RunnerTurnPipelineHost` once their
  responsibility moves to admission/persistence runtimes.
- Keep compatibility shims out of the implementation; this branch allows
  breaking internal interface cleanup.

## Expected Advantages

- The foreground authored pipeline becomes easier to reason about: input,
  provider/tool loop, output.
- Session admission and turn finalization become independently testable host
  modules.
- Runner becomes a wiring object instead of the hidden owner of every turn
  detail.
- Future schedule, child-agent, and operator gateway changes can reuse
  admission/finalization without reaching through a massive host protocol.

## Validation

Add focused tests for admission and persistence modules, then run:

```bash
uv run pytest tests/runtime/test_turn_engine.py tests/runtime/test_modular_io.py tests/runtime/test_turn_lifecycle.py
uv run python -m compileall demiurge/runtime tests/runtime
git diff --check -- demiurge/runtime tests/runtime goal/now
```

If the change touches session projections or delivery behavior, also run the
affected `tests/app/test_runtime_init.py` and delivery/outbox tests.

## Scope Boundaries

Do not change authored Agent Core layout, Agent Slot concepts, or Package
installation semantics. Do not add migration compatibility for old internal
runner host methods.
