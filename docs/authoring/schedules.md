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
prompt: "Write a daily project summary."
```

The file stem is the schedule id. A schedule is enabled unless it sets
`enabled: false`. Cron expressions are interpreted in the host runtime timezone,
not in a timezone declared by the schedule file.

## Defaults

```yaml
enabled: true
modules:
  input: [base_input]
  output: [base_output]
delivery:
  mode: local
```

`modules.input` and `modules.output` are schedule-local serial lists. They do
not run the core's full pipeline and do not run parallel modules.

## Runtime Timezone

Schedules do not accept a `timezone` field. The host resolves one runtime
timezone for cron, tools, terminal commands, and authored module metadata:

```text
--timezone
DEMIURGE_TIMEZONE
<home>/config.yaml runtime.timezone
server-local timezone
```

Durable scheduler state and run logs still store UTC instants. Run metadata also
includes local formatted due/scheduled times plus the runtime timezone source.

## External Delivery

Telegram keeps its compatibility target field:

```yaml
delivery:
  mode: telegram
  chat_id: 123456789
```

The target must be listed in the same core's
`channels.telegram.allowed_users` or `channels.telegram.allowed_chats`.

Other external channels use `target`:

```yaml
delivery:
  mode: matrix
  target: "!room:example.org"
```

The selected channel must be enabled/configured in the same core and the target
must pass that channel's allowlist rules.

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

## Managed Updates

The default assistant exposes the built-in `schedule_manage` tool. It can list,
create, update, enable, disable, and delete YAML files in the active core's
schedule slot.

The tool intentionally manages only the cron expression and prompt. Created
schedules explicitly write the default fields: enabled, `base_input`,
`base_output`, and local delivery. Its result includes the current runtime
timezone and source so the model knows how the cron expression will be
interpreted. Edit YAML directly when a schedule needs custom modules or external
delivery.

## Boundary

Schedules are not Hermes-style runtime-created jobs. They are authored YAML
files in the core. They are not channel slots and do not call providers
directly.
