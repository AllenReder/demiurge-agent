---
title: Runner and Context
description: Contributor notes for turn execution and provider context assembly.
---

# Runner and Context

The runner owns the turn lifecycle. Agent Core slots participate through
controlled interfaces; they do not own the lifecycle.

## Turn Flow

```text
inbound interaction
  -> create or resume session
  -> run bootstrap when needed
  -> run input pipeline
  -> assemble provider context
  -> call provider
  -> execute tool calls through ToolRuntime
  -> continue model/tool loop until final response
  -> run output pipeline
  -> record deliveries and session events
```

## Context Layers

Provider context can include:

- soul text
- skill index and loaded skills
- bootstrap output
- input module placements
- session history
- current user turn
- tool call and tool result history

The context assembler decides final provider message order and content.

## Bootstrap

Bootstrap modules are session-start context producers. They should be stable
within a session and safe to quote as reference context.

## Background Task Completion Turns

The runner preserves one active turn per session. Background task completion is
modeled as a synthetic inbound event for the originating session rather than as
direct channel output. Channel bridges use live subscription as a wakeup path
and recover pending completion events from SQLite. If user input and completion
are both pending, the user input runs first and pending completion summaries are
merged into that user turn. Completion notifications use durable work state:
`ready` work is claimed before a bridge queues or merges the synthetic inbound,
and it is acknowledged only through the task-worker seam. A successful
`yield_until` call claims and acknowledges the matching pending completion, so
channel bridges do not run a second synthetic completion turn for the same task
result.

Parallel input and output slots are still scheduled concurrently, but the
runner waits for their host-managed work to finish before marking the parent
turn terminal. Detached slot work must be modeled as a child runtime task rather
than mutating after the parent turn is complete.

`/stop` and foreground cancellation affect only the active turn. Background
tasks continue until they finish or a user calls `task_control(command="cancel")`.

Background work that needs user input is marked `blocked_needs_user` and is
not auto-approved.

## Failure Handling

Slot `failure_policy` determines whether a failed slot is soft or hard. Provider
errors and cancellation after a turn starts write terminal turn and task state
before the exception is re-raised. Tool errors, channel delivery errors, and
schedule errors are handled at their host-owned layers.

## Boundary

Do not move provider request construction or session ownership into Agent Core
code.
