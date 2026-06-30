# Mattermost Channel

The Mattermost channel accepts slash-command or outgoing-webhook style requests
and replies with either the Mattermost REST API or an incoming webhook URL.

## Configure

REST API mode:

```yaml
channels:
  mattermost:
    enabled: true
    base_url: https://mattermost.example.com
    token_env: MATTERMOST_BOT_TOKEN
    webhook_token_env: MATTERMOST_WEBHOOK_TOKEN
    host: 127.0.0.1
    port: 8767
    path: /mattermost
    allowed_channels: []
    allowed_users: []
```

Incoming webhook reply mode:

```yaml
channels:
  mattermost:
    enabled: true
    incoming_webhook_url_env: MATTERMOST_INCOMING_WEBHOOK_URL
    webhook_token_env: MATTERMOST_WEBHOOK_TOKEN
```

## Behavior

- Requires a shared webhook token before accepting inbound requests.
- Normalizes `text`, `channel_id`, `user_id`, optional `team_id`, and optional
  thread/root ids.
- Strips `trigger_word` from the beginning of the text when Mattermost sends it.
- Sends plain text replies.

## Scheduled Delivery

```yaml
delivery:
  mode: mattermost
  target: channel-id
```

If `allowed_channels` is configured, the target must be listed there.
