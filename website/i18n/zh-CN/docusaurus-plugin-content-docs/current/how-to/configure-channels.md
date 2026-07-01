---
title: 配置渠道
description: 为 Agent Core 启用外部 gateway channels。
---

# 配置渠道

External channels 默认是禁用的。只启用你打算暴露的 channels。

## 启动 Gateway

```bash
uv run demiurge gateway --core assistant
```

gateway 会为所选 core 运行已启用的 external channels。

## Telegram

```yaml
channels:
  telegram:
    enabled: true
    bot_token_env: DEMIURGE_TELEGRAM_BOT_TOKEN
    allowed_users:
      - 123456789
    allowed_chats: []
    reply_to_mode: "off"
```

```bash
export DEMIURGE_TELEGRAM_BOT_TOKEN="..."
uv run demiurge gateway --core assistant
```

Telegram 是 deny-by-default 的。在暴露 bot 之前，先添加允许的 users 或 chats。

## Webhook

```yaml
channels:
  webhook:
    enabled: true
    host: 127.0.0.1
    port: 8765
    path: /demiurge
    token_env: DEMIURGE_WEBHOOK_TOKEN
    allow_unauthenticated: false
```

```bash
export DEMIURGE_WEBHOOK_TOKEN="..."
uv run demiurge gateway --core assistant
```

## Slack、Mattermost、Matrix 和 Email

core manifest 支持这些 channel section：

- `slack`
- `mattermost`
- `matrix`
- `email`

每个 channel 都有自己的 token fields、allowlist fields，以及 polling 或 HTTP
行为。尽量把 secrets 放在 environment variables 中。

## 验证

```bash
uv run demiurge init --check
uv run demiurge gateway --core assistant --provider fake
```

在本地 run 中使用 logs 和 `/status` 来确认选中的 core、workspace 和 provider，然后
再对外暴露 channel。

## 边界

Channels 会把外部事件转换为 host-owned inbound turns。它们不会赋予 Agent Core 直接
的 network 或 filesystem authority。
