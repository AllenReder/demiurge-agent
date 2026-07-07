---
title: Configure Channels
description: Enable external gateway channels for an Agent Core.
---

# Configure Channels

Channels are configured in a concrete core's `agent.yaml`. They are not slot
modules, and the loader does not auto-scan `agent/channels/`.

Supported gateway channels are:

- Telegram
- Webhook
- Slack
- Mattermost
- Matrix
- Email

Start the gateway with:

```bash
uv run demiurge gateway --core assistant
```

Only channels with `enabled: true` are started.

## Conversation binding

Each inbound channel conversation is bound to a durable Demiurge session through
a host-owned `conversation_key`. Keys are canonical route keys, not adapter-local
strings. Older bindings are not migrated, so a channel conversation that used an
older key shape starts fresh until you explicitly resume the intended session.

The current canonical keys are:

- Telegram private chat: `telegram:dm:<chat_id>`
- Telegram group or supergroup: `telegram:group:<chat_id>`
- Slack channel: `slack:channel:<team_id>:<channel_id>`
- Slack thread: `slack:channel:<team_id>:<channel_id>:thread:<thread_ts>`
- Mattermost channel: `mattermost:channel:<channel_id>`
- Mattermost thread: `mattermost:channel:<channel_id>:thread:<root_id>`
- Matrix room: `matrix:room:<room_id>`
- Email sender: `email:sender:<sender>`
- Webhook fallback source: `webhook:source:<source>`

Route ids are URL-encoded in the stored key. Webhook requests can still provide
an explicit `conversation_key`; when present, that value is used as-is.

Channel `/resume` changes both the live route and the durable conversation
binding. After `/resume` succeeds in a channel, the next message from the same
external conversation continues in the resumed session.

## Telegram

Add or edit `channels.telegram` in `~/.demiurge/agents/assistant/agent.yaml`:

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

Then run:

```bash
export DEMIURGE_TELEGRAM_BOT_TOKEN="..."
uv run demiurge gateway --core assistant
```

Telegram is allowlist-required. Private chats require the Telegram user id in
`allowed_users`. Group chats require both the user id in `allowed_users` and the
chat id in `allowed_chats`.

In private chats, Telegram photos, voice messages, audio, video, and documents
are downloaded under the runtime workspace's `.demiurge-telegram/` cache and
exposed to input slots through `ctx.input.attachments`. The channel only
normalizes attachment metadata and local paths; transcription, OCR, image
understanding, and other interpretation belong in input-slot packages.

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

Requests authenticate with `Authorization: Bearer <token>`,
`X-Demiurge-Token`, or a `token` body field. Schedule delivery targets must be
keys in `delivery_targets`.

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

Slack requires both a bot token and signing secret. If allowlists are present,
inbound events and schedule targets must match them.

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

Mattermost requires either `base_url` plus bot token, or an incoming webhook
URL. Inbound webhooks also require `webhook_token_env` or `webhook_token`.

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

Matrix polls joined rooms. If `allowed_rooms` is set, only those room ids are
accepted and valid for schedule delivery.

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

Email requires SMTP and IMAP credentials. If `allowed_senders` is set, the
bridge also requires `trust_from_headers: true` because sender headers can be
spoofed.

## Verify

Run:

```bash
uv run demiurge init --check
uv run demiurge gateway --core assistant --provider fake
```

Use `/status` in local TUI or Telegram runs to confirm the selected core,
workspace, provider, and runtime timezone before exposing a channel.

## Boundary

Channels translate external events into host-owned inbound turns and route
host-owned deliveries back out. They do not grant the Agent Core direct network,
filesystem, provider, or approval authority.
