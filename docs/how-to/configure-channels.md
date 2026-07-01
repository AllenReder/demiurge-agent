---
title: Configure Channels
description: Enable external gateway channels for an Agent Core.
---

# Configure Channels

External channels are disabled by default. Enable only the channels you intend
to expose.

## Start the Gateway

```bash
uv run demiurge gateway --core assistant
```

The gateway runs enabled external channels for the selected core.

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

Telegram is deny-by-default. Add allowed users or chats before exposing the bot.

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

## Slack, Mattermost, Matrix, and Email

The core manifest supports these channel sections:

- `slack`
- `mattermost`
- `matrix`
- `email`

Each channel has its own token fields, allowlist fields, and polling or HTTP
behavior. Keep secrets in environment variables where possible.

## Verify

```bash
uv run demiurge init --check
uv run demiurge gateway --core assistant --provider fake
```

Use logs and `/status` from local runs to confirm selected core, workspace, and
provider before exposing a channel.

## Boundary

Channels translate external events into host-owned inbound turns. They do not
grant the Agent Core direct network or filesystem authority.
