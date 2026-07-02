---
title: Configure a Schedule
description: Add a cron schedule to an Agent Core.
---

# Configure a Schedule

Schedules are Agent Core declarations executed by the host scheduler. Each due
run creates a fresh scheduled session and runs a configured list of authored
input and output modules.

By default, the loader looks under:

```text
agent/schedules/*.yaml
```

If `agent.yaml` sets `slots.schedules`, that value overrides the default
schedule root.

## Add a Local Schedule

Create `agent/schedules/morning-summary.yaml`:

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

Fields:

| Field | Default | Notes |
| --- | --- | --- |
| `enabled` | `true` | Disabled schedules load but are not claimed. |
| `schedule` | Required | Standard cron expression. |
| `prompt` | Required | Self-contained prompt for the scheduled turn. |
| `modules.input` | `["base_input"]` | Input slot ids to run for this schedule. |
| `modules.output` | `["base_output"]` | Output slot ids to run for this schedule. |
| `delivery.mode` | `local` | `local` records the run without channel delivery. |

Schedule YAML does not have a `timezone` field. Runtime timezone is owned by
the host runtime or process options.

## Use Existing Module IDs

The module lists must reference real input and output slot ids loaded from the
same core. For example, if you add `agent/output/send_digest/`, keep existing
module ids and append the new one where needed:

```yaml
modules:
  input:
    - base_input
  output:
    - base_output
    - send_digest
```

Do not remove `base_input` or `base_output` unless the schedule has another
slot that supplies user text or handles output.

## Deliver to a Channel

Telegram schedule delivery uses `chat_id`:

```yaml
delivery:
  mode: telegram
  chat_id: 123456789
```

That `chat_id` must appear in `channels.telegram.allowed_users` or
`channels.telegram.allowed_chats` in the concrete core manifest.

Other channels use `target`:

```yaml
delivery:
  mode: webhook
  target: project-status
```

Channel target validation is channel-specific:

| Channel | Schedule target rule |
| --- | --- |
| `webhook` | `target` must exist in `channels.webhook.delivery_targets`. |
| `slack` | `target` is a channel id; if `allowed_channels` is set, it must be listed. |
| `mattermost` | `target` is a channel id; if `allowed_channels` is set, it must be listed. |
| `matrix` | `target` is a room id; if `allowed_rooms` is set, it must be listed. |
| `email` | `target` is an email address; if `allowed_recipients` is set, it must be listed. |

The corresponding channel must be configured in `agent.yaml`.

## Verify

Run the loader check:

```bash
uv run demiurge init --check
```

Run the gateway when schedules should fire in a live channel process:

```bash
uv run demiurge gateway --core assistant --provider fake
```

The model-facing `schedule_manage` tool can create, update, enable, disable,
delete, and list schedule YAML files when the core exposes the `schedule`
toolset and has `schedule.manage`.

## Boundary

Schedules declare cron intent, prompt, module ids, and delivery target. The host
owns due-time calculation, claims, run logs, scheduled sessions, channel
validation, and delivery.
