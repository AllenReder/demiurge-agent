# Runner and Context

The runner owns session, turn, phase, model-step, tool-call, and output
execution. Agent cores provide declarations and code slots; the runner decides
when and how they execute.

## Runner Responsibilities

`SessionTurnStepRunner` coordinates:

- session creation/resume;
- bootstrap snapshot generation;
- input pipeline execution;
- context assembly;
- provider requests;
- tool-call/result loop;
- output pipeline execution;
- delivery and event recording;
- child core `run`/`spawn` calls.

## Phase Order

```text
ensure session
  -> run bootstrap once if configured
  -> run input serial then parallel
  -> assemble context
  -> call provider
  -> execute requested tools
  -> repeat until final response or max_model_steps
  -> run output serial then parallel
  -> return TurnResult
```

For channel turns, the provider step may use provider response streaming when
the active bridge and default persistent `base_output` path can display it. The
runner still owns request construction, streamed tool-call delta assembly, tool
execution, and final output delivery; agent cores do not call provider streaming
APIs or channel renderers directly.

## Context Layers

`ContextAssembler` builds provider messages in this order:

1. core soul;
2. skill index;
3. bootstrap context;
4. input contributions with `system_context` placement;
5. compaction summary;
6. input contributions with `pre_history` placement;
7. session history;
8. input contributions with `pre_current_user` placement;
9. current turn and `post_current_user` contributions.

## Tool Calls in History

Assistant tool-call steps and tool results are written by the host so later
provider requests can reconstruct valid tool-call/result pairs. Tool results
are usually hidden from the user but model-visible.

## Bootstrap Snapshot

Bootstrap context is a system-prompt layer, not a transcript message. It is
stored in `bootstrap_context.md`, reused on resume, and not compacted.

## Failure Modes

- Missing input/output pipeline blocks core load.
- Hard bootstrap or module failures block the relevant model request/phase.
- A schedule run that requests user input is recorded as an error.
- Exceeding `max_model_steps` stops the model loop.
