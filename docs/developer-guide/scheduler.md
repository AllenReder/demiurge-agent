# Scheduler Internals

The scheduler is host-owned. Agent cores declare schedules, but the host claims,
runs, logs, and delivers them.

## Runtime Files

```text
~/.demiurge/scheduler/<core_id>/
  state.json
  runs.jsonl
  lock
```

`state.json` stores next-run state and schedule signatures. `runs.jsonl` stores
claim/completion/error events. `lock` prevents overlapping claims.

## Claim Flow

```text
load active core
  -> scan enabled schedules
  -> compare signature and next_run_at
  -> claim one due run under file lock
  -> advance next_run_at
  -> run fresh session
  -> record completed/error
```

If a schedule signature changes, the scheduler resets next run state and does
not immediately run the changed schedule.

Long-running TUI and gateway processes start the scheduler loop even when the
active core has no schedules yet. This lets newly authored schedule YAML become
visible on a later scan without restarting the process.

## Run Flow

Each claimed run creates a fresh `SessionTurnStepRunner` with a schedule session
id. The prompt becomes `raw_input.text`. Metadata includes trigger, schedule id,
run id, due time, scheduled time, and delivery mode.

Schedule module lists are serial-only overrides for that run. They do not run
the full core pipeline or parallel modules.

## Delivery

`local` delivery records session output and scheduler logs only. External
channel delivery validates the target against the same core's channel config and
routes output through the active gateway bridge when available. If the scheduler
runs without an active gateway bridge, it builds the configured channel bridge on
demand.

Telegram uses `chat_id`; other external channels use `target`.

## Failure Modes

- Interactive clarification or approval in a schedule run fails closed.
- External delivery without an allowed target records an error.
- Process downtime coalesces missed fires into one claimed run.
