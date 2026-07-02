---
title: Configure a Schedule
description: Add a cron schedule to an Agent Core.
---

# Configure a Schedule

Schedules are declared by an Agent Core and executed by the host scheduler. Each
run creates a fresh scheduled session.

## Add a Local Schedule

Create:

```text
agent/schedules/morning-summary.yaml
```

```yaml
enabled: true
schedule: "0 9 * * *"
prompt: "Summarize the current project state and list one next action."
modules:
  input:
    - base_input
  output:
    - base_output
delivery:
  mode: local
```

The `schedule` field is a cron expression. Runtime timezone comes from host
runtime timezone configuration or a process `--timezone` override.

## Deliver to a Channel

Telegram delivery:

```yaml
delivery:
  mode: telegram
  chat_id: 123456789
```

Other channels use `target`:

```yaml
delivery:
  mode: webhook
  target: project-status
```

The corresponding channel must be enabled in `agent.yaml`, and the channel must
accept the target.

## Verify

```bash
uv run demiurge init --check
uv run demiurge gateway --core assistant
```

Schedules are host-owned runtime runs. The schedule toolset can manage schedule
files through approved host capabilities when it is enabled for the core.

## Boundary

Schedules declare intent and delivery target. The host owns claims, run logs,
session creation, channel validation, and delivery.
