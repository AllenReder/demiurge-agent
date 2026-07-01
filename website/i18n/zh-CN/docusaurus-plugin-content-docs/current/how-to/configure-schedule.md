---
title: 配置调度
description: 为 Agent Core 添加 cron schedule。
---

# 配置调度

Schedules 由 Agent Core 声明，由 host scheduler 执行。每次 run 都会创建一个新的
scheduled session。

## 添加本地 Schedule

创建：

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

`schedule` 字段是 cron expression。Runtime timezone 来自 host runtime timezone
configuration，或者进程的 `--timezone` override。

## 投递到 Channel

Telegram delivery：

```yaml
delivery:
  mode: telegram
  chat_id: 123456789
```

其他 channels 使用 `target`：

```yaml
delivery:
  mode: webhook
  target: project-status
```

对应的 channel 必须在 `agent.yaml` 中启用，而且 channel 必须接受这个 target。

## 验证

```bash
uv run demiurge init --check
uv run demiurge gateway --core assistant
```

Schedules 是 host-owned 的 runtime jobs。schedule toolset 在为 core 启用时，可以通过受
批准的 host capabilities 管理 schedule files。

## 边界

Schedules 声明 intent 和 delivery target。host 拥有 claims、run logs、session
creation、channel validation 和 delivery。
