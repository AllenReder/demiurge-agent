---
title: Scheduler
description: Contributor notes for host-owned schedule claims and runs.
---

# Scheduler

Schedules are declared by Agent Cores and executed by the host.

## Runtime State

Core-authored schedules live under:

```text
agent/schedules/*.yaml
```

Host scheduler state is projected into:

```text
~/.demiurge/runtime/runtime.sqlite3
```

The `scheduler_instances` projection records due times, task ids, claim status,
and idempotency keys. Older `~/.demiurge/scheduler/` JSON files are not read,
migrated, or dual-written by the runtime.

## Claim Flow

The scheduler computes due times from cron expressions and runtime timezone.
When a schedule is due, the host records a transactional SQLite claim, advances
the next run time, and creates a runtime task using an idempotency key derived
from core id, schedule id, and due time.

## Run Flow

Each run creates a fresh scheduled session with synthetic inbound metadata. The
runner executes the schedule prompt using the schedule-selected input and output
modules.

## Delivery

Local delivery stays in local session records. External delivery validates the
configured channel and target before sending.

## Boundary

The Agent Core declares schedules. The host owns durable job state, claims, run
records, session creation, and channel delivery.
