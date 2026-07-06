---
title: Operator Gateway
description: Contributor notes for the local TUI/dashboard gateway runtime.
---

# Operator Gateway

`OperatorGatewayRuntime` is the Python-side product gateway for local operator
clients such as the TUI and future dashboard surfaces. It is not a messaging
channel.

## Responsibilities

The operator gateway owns local product state and control-plane views:

- session context for the local operator client;
- prompt and approval pending state;
- slash command routing for local operator commands;
- status, history, and host-work projections;
- scheduler lifecycle for the local app process;
- busy, queue, and interrupt handling through `ConversationLifecycleRuntime`;
- interaction route binding for the active operator session.

`TuiInteractionBridge` is now a narrow adapter. It forwards TUI RPC methods to
`OperatorGatewayRuntime` and keeps the existing RPC-facing class name for the
launcher and tests.

## Event Shape

Operator clients should prefer product events for UI state:

- `operator.ready`
- `operator.status`
- `operator.history`
- `operator.work.updated`
- `operator.prompt.opened`
- `operator.approval.opened`
- `operator.error`

`interaction.*` events remain the transcript and compatibility stream:

- user and assistant transcript delivery;
- tool-call display records;
- current TUI reducer compatibility with older gateway frames.

The TUI reducer accepts both `operator.*` and legacy `interaction.*` state
events. New dashboard code should use the `operator.*` names for product state
and keep `interaction.deliver` for transcript output.

## Boundary With Channels

Messaging channels own external platform concerns: allowlists, remote user and
thread routing, webhook or polling lifecycle, platform delivery, and
`run_forever()`.

The operator gateway owns local control concerns: sessions, runtime status,
tasks, packages, schedules, approvals, prompts, and host-work observability.
It may use `InteractionInbound` and `InteractionOutbound` to share the same turn
entry and delivery objects as channels, but the TUI/dashboard is not modeled as
a `Channel`.

## Long Commands

The NDJSON gateway entrypoint isolates selected long operator commands such as
`/doctor`, `/packages`, `/evolve`, `/rollback`, and `/compact` from the RPC read
loop. This keeps prompt replies, approval replies, and interrupts responsive
while a slow operator command is running.
