---
title: Delivery Runtime
description: Contributor notes for session records, live output, artifacts, and channels.
---

# Delivery Runtime

Delivery runtime converts output requests into durable session records, live
events, artifacts, and channel items.

Every output `send_*` call also writes a delivery intent into the SQLite
runtime `outbox` projection and a matching `delivery.send` durable work item.
Outbox rows are owned by the foreground turn through `owner_turn_id`; delivery
work does not use the foreground turn id as a task id or parent work id.
`DeliveryRuntime` claims and completes the durable work through
`HostWorkLifecycleRuntime`, then owns dispatch through
`SessionInteractionRouter`, which looks up the active route for
`InteractionOutbound.session_id`. Channel adapters are bound to sessions; they
adapt payloads to platform APIs but do not own durable delivery state.

The in-memory `InteractionItem.dispatch_status` lifecycle is
`pending -> scheduled -> delivered/failed/unrouted`. The durable outbox
lifecycle is `queued -> sending -> sent/failed/unknown/unrouted`.
`unrouted` means no route is currently bound for the outbound session. It is
different from `failed`, which means a route existed and adapter delivery raised
an error.

## Sources

Delivery requests can come from:

- output modules
- authored tools
- schedule runs
- channel adapter logic

## History Policy

Persisted delivery becomes durable assistant history. Transient delivery is
useful for progress, notices, and live-only output.

## Artifacts

Artifacts are represented by host-owned records. Output modules may request
artifact delivery, but the host owns paths, metadata, and persistence.
Foreground delivery artifacts are owned by the session turn through
`owner_turn_id`; they are not task artifacts unless a real detached task creates
them through a future explicit task-artifact seam.

## Session Routes

`InteractionOutbound.session_id` is required. The `channel` field is adapter
metadata; it no longer decides route ownership. Ordinary live delivery is routed
only by `outbound.session_id`.

`SessionInteractionRouter` owns the live route table:

- `bind(session_id, route)` attaches a TUI, Telegram, or other adapter route to
  one session and returns a token.
- `unbind(token)` removes that live route.
- `deliver(outbound)` sends only to the route bound for `outbound.session_id`.
- `prompt_user(prompt)` and `request_approval(request)` perform separate
  session-aware route lookups for interactive prompts and approvals.

Routes defensively reject outbound payloads for any session other than the one
they are bound to. `InteractionRuntime.handle()` binds the inbound route after
the runner resolves the final session. `/new`, `/resume`, and session switch
paths must rebind to the new session.

## Channels

Channel adapters adapt delivery into platform-specific messages. They no longer
act as ambient bridges inherited by nested work. If a session has no active
route, ordinary deliveries and tool lifecycle items are marked `unrouted` and
are not returned as pending fallback output from `InteractionRuntime.handle()`.

If bridge delivery fails after history was written, the history row remains
durable. Non-text delivery with `write_history=True` must provide explicit
`history_text`; the host does not invent artifact placeholder text. Optional
`failure_history_text` can replace the history row on first failure. Later retry
status updates must not rewrite that body.

The host claims a delivery before platform I/O starts. If the process crashes
after `sending` and before a platform result is durably recorded, recovery marks
that delivery `unknown` instead of replaying it automatically. A channel that
can reconcile platform state may resolve `unknown`; otherwise it remains
operator-visible state.

Operator-facing status should be read through `HostWorkLifecycleRuntime`
instead of separately joining `outbox` and `runtime_work_items`. The lifecycle
view reports the effective delivery status while retaining the underlying
durable claim state for debugging.

## Subagents

Child agent runs use separate `session_child_*` sessions. The router does not
know parent/child lineage. Child ordinary output and tool lifecycle delivery go
only to a route explicitly bound for the child session. Without such a route,
they are `unrouted`; the parent sees the child only through explicit
`AgentRunResult`, task completion, or future observability events.

## Boundary

Do not let output modules write session history or channel state directly.
Do not route delivery by parent/child relationships, conversation keys, or
ambient adapter state.
