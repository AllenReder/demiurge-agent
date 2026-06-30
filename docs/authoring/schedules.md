# Schedules

Schedules are cron declarations in an agent core. The host scheduler executes
them by starting a fresh session and running one normal turn through selected
input/output modules.

## Minimal Schedule

Create:

```text
agent/schedules/daily_summary.yaml
```

```yaml
schedule: "0 9 * * *"
timezone: "UTC"
prompt: "Write a daily project summary."
```

The file stem is the schedule id. A schedule is enabled unless it sets
`enabled: false`.

## Defaults

```yaml
enabled: true
timezone: "UTC"
modules:
  input: [base_input]
  output: [base_output]
delivery:
  mode: local
```

`modules.input` and `modules.output` are schedule-local serial lists. They do
not run the core's full pipeline and do not run parallel modules.

## Telegram Delivery

```yaml
delivery:
  mode: telegram
  chat_id: 123456789
```

The target must be listed in the same core's
`channels.telegram.allowed_users` or `channels.telegram.allowed_chats`.

## Runtime Semantics

State and run logs live under:

```text
~/.demiurge/scheduler/<core_id>/
  state.json
  runs.jsonl
  lock
```

Missed fires coalesce. If the process was down for several cron times, the next
scan claims one run and advances to the next future fire.

## Success Check

Start a long-running TUI or gateway process for the selected core. Then inspect:

```bash
tail -n 20 ~/.demiurge/scheduler/<core_id>/runs.jsonl
```

## Boundary

Schedules are not Hermes-style runtime-created jobs. They are authored YAML
files in the core. They are not channel slots and do not call providers
directly.
