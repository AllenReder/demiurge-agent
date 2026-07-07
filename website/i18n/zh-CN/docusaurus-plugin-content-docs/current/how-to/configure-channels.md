---
title: 配置 Channels
description: 为 Agent Core 启用 external gateway channels。
---

# 配置 Channels

Channels 在具体 core 的 `agent.yaml` 中配置。它们不是 slot modules，loader 也不会自动扫描 `agent/channels/`。

支持的 gateway channels 有：

- Telegram
- Webhook
- Slack
- Mattermost
- Matrix
- Email

使用以下命令启动 gateway：

```bash
uv run demiurge gateway --core assistant
```

只有设置了 `enabled: true` 的 channels 会被启动。

## Conversation binding

每个 inbound channel conversation 都会通过 host-owned `conversation_key`
绑定到一个持久 Demiurge session。Key 是规范 route key，不是各 adapter
自行拼出的本地字符串。旧 binding 不会迁移，所以使用旧 key 形状的 channel
conversation 会重新开始，除非你显式 resume 到目标 session。

当前规范 key 是：

- Telegram private chat：`telegram:dm:<chat_id>`
- Telegram group 或 supergroup：`telegram:group:<chat_id>`
- Slack channel：`slack:channel:<team_id>:<channel_id>`
- Slack thread：`slack:channel:<team_id>:<channel_id>:thread:<thread_ts>`
- Mattermost channel：`mattermost:channel:<channel_id>`
- Mattermost thread：`mattermost:channel:<channel_id>:thread:<root_id>`
- Matrix room：`matrix:room:<room_id>`
- Email sender：`email:sender:<sender>`
- Webhook fallback source：`webhook:source:<source>`

Route ids 会在存储的 key 中做 URL encoding。Webhook requests 仍然可以显式提供
`conversation_key`；提供时会原样使用这个值。

Channel `/resume` 会同时切换 live route 和持久 conversation binding。Channel 中
`/resume` 成功后，同一个 external conversation 的下一条消息会继续进入被 resume 的
session。

## Telegram

在 `~/.demiurge/agents/assistant/agent.yaml` 中添加或编辑 `channels.telegram`：

```yaml
channels:
  telegram:
    enabled: true
    bot_token_env: DEMIURGE_TELEGRAM_BOT_TOKEN
    allowed_users:
      - 123456789
    allowed_chats: []
    unauthorized_response: brief
    poll_timeout: 30
    message_format: markdown_v2
    register_commands: true
    send_typing: true
    rich_messages: true
    reply_to_mode: "off"
```

然后运行：

```bash
export DEMIURGE_TELEGRAM_BOT_TOKEN="..."
uv run demiurge gateway --core assistant
```

Telegram 需要 allowlist。Private chats 要求 Telegram user id 位于 `allowed_users` 中。Group chats 同时要求 user id 位于 `allowed_users`，并且 chat id 位于 `allowed_chats`。

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
    callback_url_env: null
    callback_url: null
    allow_private_callback_urls: false
    allowed_sources: []
    delivery_targets:
      project-status: https://example.test/status-callback
```

```bash
export DEMIURGE_WEBHOOK_TOKEN="..."
uv run demiurge gateway --core assistant
```

Requests 使用 `Authorization: Bearer <token>`、`X-Demiurge-Token` 或 body 中的 `token` 字段进行认证。Schedule delivery targets 必须是 `delivery_targets` 中的 keys。

## Slack

```yaml
channels:
  slack:
    enabled: true
    bot_token_env: SLACK_BOT_TOKEN
    signing_secret_env: SLACK_SIGNING_SECRET
    host: 127.0.0.1
    port: 8766
    path: /slack/events
    bot_user_id: U0123456789
    app_mentions_only: true
    allowed_teams: []
    allowed_channels: []
    allowed_users: []
```

Slack 同时需要 bot token 和 signing secret。如果存在 allowlists，inbound events 和 schedule targets 都必须匹配它们。

## Mattermost

```yaml
channels:
  mattermost:
    enabled: true
    base_url: https://mattermost.example
    token_env: MATTERMOST_BOT_TOKEN
    incoming_webhook_url_env: null
    webhook_token_env: MATTERMOST_WEBHOOK_TOKEN
    host: 127.0.0.1
    port: 8767
    path: /mattermost
    allowed_channels: []
    allowed_users: []
```

Mattermost 需要 `base_url` 加 bot token，或一个 incoming webhook URL。Inbound webhooks 还需要 `webhook_token_env` 或 `webhook_token`。

## Matrix

```yaml
channels:
  matrix:
    enabled: true
    homeserver_url: https://matrix.example
    access_token_env: MATRIX_ACCESS_TOKEN
    user_id: "@demiurge:example"
    allowed_rooms: []
    poll_timeout: 30
```

Matrix 会轮询 joined rooms。如果设置了 `allowed_rooms`，只有这些 room ids 会被接受，并且可用于 schedule delivery。

## Email

```yaml
channels:
  email:
    enabled: true
    smtp_host: smtp.example
    smtp_port: 587
    smtp_starttls: true
    smtp_username_env: DEMIURGE_SMTP_USERNAME
    smtp_password_env: DEMIURGE_SMTP_PASSWORD
    imap_host: imap.example
    imap_port: 993
    imap_username_env: DEMIURGE_IMAP_USERNAME
    imap_password_env: DEMIURGE_IMAP_PASSWORD
    mailbox: INBOX
    from_address: null
    allowed_senders: []
    allowed_recipients: []
    trust_from_headers: false
    poll_interval: 30
```

Email 需要 SMTP 和 IMAP credentials。如果设置了 `allowed_senders`，bridge 也要求 `trust_from_headers: true`，因为 sender headers 可能被伪造。

## 验证

运行：

```bash
uv run demiurge init --check
uv run demiurge gateway --core assistant --provider fake
```

在 local TUI 或 Telegram runs 中使用 `/status`，在暴露 channel 之前确认选定的 core、workspace、provider 和 runtime timezone。

## 边界

Channels 会把 external events 转换为 host-owned inbound turns，并把 host-owned deliveries 路由出去。它们不会授予 Agent Core 直接 network、filesystem、provider 或 approval 权限。
