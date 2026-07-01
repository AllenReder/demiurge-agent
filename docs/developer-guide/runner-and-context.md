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

## Failure Handling

Slot `failure_policy` determines whether a failed slot is soft or hard. Provider
errors, tool errors, channel delivery errors, and schedule errors are handled at
their host-owned layers.

## Boundary

Do not move provider request construction or session ownership into Agent Core
code.
