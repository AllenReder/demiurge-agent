# Schedules

Schedules are cron jobs declared by an agent core and executed by the host
scheduler. A schedule is not a channel slot and does not call providers
directly. Each fire creates a fresh agent session and runs one normal turn
through selected input/output modules.

## Authoring

Create YAML files under `agent/schedules/`:

```text
agent/schedules/daily_summary.yaml
```

```yaml
schedule: "0 9 * * *"
timezone: "UTC"
prompt: "Write a daily project summary."
```

The file stem is the schedule id. A file is enabled by default unless it sets
`enabled: false`.

Defaults:

```yaml
enabled: true
timezone: "UTC"
modules:
  input: [base_input]
  output: [base_output]
delivery:
  mode: local
```

`modules.input` and `modules.output` must reference existing input/output
modules. These lists are schedule-local serial lists. They do not run the
core's full pipeline and do not run parallel modules.

A core can override the schedule root in `agent.yaml`:

```yaml
slots:
  schedules: agent/schedules
```

## Runtime Semantics

The scheduler starts with long-running TUI and configured channel processes. It
scans only the selected core. State and run logs live under:

```text
~/.demiurge/scheduler/<core_id>/
  state.json
  runs.jsonl
  lock
```

Each run enters the normal runner with `prompt` as `raw_input.text`. Metadata
includes `trigger=schedule`, `schedule_id`, `run_id`, `due_at`, `scheduled_at`,
and `delivery_mode`.

Missed fires coalesce. If the process was down for several cron times, the next
scan claims one run and advances `next_run_at` to the next future fire. File
locks and durable claim updates avoid two processes claiming the same due fire.

Cron runs are non-interactive. A tool result that needs user input marks the run
as an error. Approval fails closed unless host policy can auto-approve without
an Interaction Bridge.

## Delivery

With no `delivery` block, or with `delivery.mode: local`, output is recorded in
the session and scheduler run log only.

Telegram delivery must declare a target:

```yaml
delivery:
  mode: telegram
  chat_id: 123456789
```

The target must be listed in the same core's
`channels.telegram.allowed_users` or `channels.telegram.allowed_chats`.
Telegram delivery reuses the host `TelegramInteractionBridge.deliver()` path.
It does not create a user-input turn and does not wait for interactive clarify
or approval.

Hermes-style runtime-created jobs and `origin` are not part of the current
schedule model.
