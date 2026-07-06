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

The NDJSON launcher instantiates `OperatorGatewayRuntime` directly. There is no
compatibility bridge class or legacy TUI protocol facade.

## Event Shape

Operator clients should prefer product events for UI state:

- `operator.ready`
- `operator.status`
- `operator.history`
- `operator.work.updated`
- `operator.prompt.opened`
- `operator.approval.opened`
- `operator.error`
- `operator.message`
- `operator.deliver`
- `operator.shutdown`

The TUI reducer consumes only `operator.*` frames. Internal
`InteractionInbound` and `InteractionOutbound` objects are still shared with
messaging channels below the gateway, but those names are not the operator wire
protocol.

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
