---
title: Delivery Runtime
description: Contributor notes for session records, live output, artifacts, and channels.
---

# Delivery Runtime

Delivery runtime converts output requests into durable session records, live
events, artifacts, and channel items.

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

## Boundary

Do not let output modules write session history or channel state directly.
