---
title: 配置 Schedule
description: 向 Agent Core 添加 cron schedule。
---

# 配置 Schedule

Schedules 是由 host scheduler 执行的 Agent Core declarations。每次 due run 都会创建一个新的 scheduled session，并运行配置好的 authored input 和 output modules 列表。

默认情况下，loader 会查找：

```text
agent/schedules/*.yaml
```

如果 `agent.yaml` 设置了 `slots.schedules`，该值会覆盖默认 schedule root。

## 添加 Local Schedule

创建 `agent/schedules/morning-summary.yaml`：

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

字段：

| Field | Default | Notes |
| --- | --- | --- |
| `enabled` | `true` | Disabled schedules 会被加载，但不会被 claim。 |
| `schedule` | Required | 标准 cron expression。 |
| `prompt` | Required | Scheduled turn 使用的自包含 prompt。 |
| `modules.input` | `["base_input"]` | 这个 schedule 要运行的 input slot ids。 |
| `modules.output` | `["base_output"]` | 这个 schedule 要运行的 output slot ids。 |
| `delivery.mode` | `local` | `local` 会记录 run，但不进行 channel delivery。 |

Schedule YAML 没有 `timezone` 字段。Runtime timezone 由 host runtime 或 process options 拥有。

## 使用现有 Module IDs

Module lists 必须引用同一个 core 中已加载的真实 input 和 output slot ids。例如，如果你添加了 `agent/output/send_digest/`，请保留现有 module ids，并在需要的位置追加新的 id：

```yaml
modules:
  input:
    - base_input
  output:
    - base_output
    - send_digest
```

除非 schedule 有另一个 slot 负责提供 user text 或处理 output，否则不要移除 `base_input` 或 `base_output`。

## 发送到 Channel

Telegram schedule delivery 使用 `chat_id`：

```yaml
delivery:
  mode: telegram
  chat_id: 123456789
```

该 `chat_id` 必须出现在具体 core manifest 的 `channels.telegram.allowed_users` 或 `channels.telegram.allowed_chats` 中。

其他 channels 使用 `target`：

```yaml
delivery:
  mode: webhook
  target: project-status
```

Channel target validation 取决于 channel：

| Channel | Schedule target rule |
| --- | --- |
| `webhook` | `target` 必须存在于 `channels.webhook.delivery_targets`。 |
| `slack` | `target` 是 channel id；如果设置了 `allowed_channels`，它必须列在其中。 |
| `mattermost` | `target` 是 channel id；如果设置了 `allowed_channels`，它必须列在其中。 |
| `matrix` | `target` 是 room id；如果设置了 `allowed_rooms`，它必须列在其中。 |
| `email` | `target` 是 email address；如果设置了 `allowed_recipients`，它必须列在其中。 |

对应的 channel 必须在 `agent.yaml` 中配置。

## 验证

运行 loader 检查：

```bash
uv run demiurge init --check
```

当 schedules 应该在 live channel process 中触发时，运行 gateway：

```bash
uv run demiurge gateway --core assistant --provider fake
```

当 core 暴露 `schedule` toolset 且拥有 `schedule.manage` 时，面向模型的 `schedule_manage` tool 可以创建、更新、启用、禁用、删除和列出 schedule YAML files。

## 边界

Schedules 声明 cron intent、prompt、module ids 和 delivery target。Host 拥有 due-time calculation、claims、run logs、scheduled sessions、channel validation 和 delivery。
