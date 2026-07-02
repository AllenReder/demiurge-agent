---
title: Scheduler
description: Contributor notes for host-owned schedule claims and runs.
---

# Scheduler

Schedules are declared by Agent Cores and executed by the host.

## Runtime Files

Core-authored schedules live under:

```text
agent/schedules/*.yaml
```

Host scheduler state lives under:

```text
~/.demiurge/scheduler/<core_id>/
```

The current scheduler still uses this JSON state as the claim adapter, but each
claimed run also submits a `schedule.fire` task to `RuntimeControlPlane` and
updates the SQLite `scheduler_instances` projection.

## Claim Flow

The scheduler computes due times from cron expressions and runtime timezone.
When a schedule is due, the host records a claim, advances the next run time,
and creates a runtime task using an idempotency key derived from core id,
schedule id, and due time.

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
