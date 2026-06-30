# Telegram

Telegram is an external channel started by `demiurge gateway`. It is
deny-by-default and configured in the selected concrete core.

## Enable Telegram

In `~/.demiurge/agents/assistant/agent.yaml`:

```yaml
channels:
  telegram:
    enabled: true
    bot_token_env: DEMIURGE_TELEGRAM_BOT_TOKEN
    bot_username: your_bot
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

Run:

```bash
export DEMIURGE_TELEGRAM_BOT_TOKEN="..."
uv run demiurge gateway --core assistant
```

## Authorization

- Private chats require sender `from.id` in `allowed_users`.
- Groups and supergroups require both sender `from.id` and `chat.id`.
- Without an allowlist, messages and callbacks are rejected.

Use numeric Telegram ids. Do not use usernames for authorization.

## Approvals

Telegram private chats support approval prompts with inline buttons:

- `Allow once`
- `Allow for session`
- `Deny`

Telegram group chats do not currently support interactive approvals; actions
that need approval fail closed.

## Reply Anchoring

`reply_to_mode` controls Telegram reply references only:

- `"off"`
- `"first"`
- `"all"`

Quote `"off"` in YAML. Bare `off` can be parsed as boolean `false`.

## Schedule Delivery

Schedules can proactively deliver to Telegram:

```yaml
delivery:
  mode: telegram
  chat_id: 123456789
```

The target must be allowed in the same core.

## Success Check

```bash
uv run demiurge gateway --core assistant
```

Send `/status` or a normal message from an allowed private chat. If nothing
responds, check `allowed_users`, token env, and gateway startup output.
