---
title: Delivery Runtime
description: Contributor notes for session records, live output, artifacts, and channels.
---

# Delivery Runtime

Delivery runtime converts output requests into durable session records, live
events, artifacts, and channel items.

Every output `send_*` call also writes a delivery intent into the SQLite
runtime `outbox` projection and a matching `delivery.send` durable work item.
`DeliveryRuntime` owns dispatch through the active channel bridge and records
`sending`, `sent`, `failed`, or `unknown` status back to the outbox. Channel
bridges adapt payloads to platform APIs; they do not own durable delivery
state.

## Sources

Delivery requests can come from:

- output modules
- authored tools
- schedule runs
- channel bridge logic

## History Policy

Persisted delivery becomes durable assistant history. Transient delivery is
useful for progress, notices, and live-only output.

## Artifacts

Artifacts are represented by host-owned records. Output modules may request
artifact delivery, but the host owns paths, metadata, and persistence.

## Channels

Channel bridges adapt delivery into platform-specific messages. They also carry
route context for scheduled and asynchronous delivery.

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

## Boundary

Do not let output modules write session history or channel state directly.
