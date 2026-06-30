# Slack Channel

The Slack channel supports Slack Events API callbacks and slash-command style
form posts over the local gateway HTTP server, then sends replies with
`chat.postMessage`.

Socket Mode is intentionally not implemented because Demiurge does not add a
websocket dependency.

## Configure

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

Expose the configured endpoint to Slack with your reverse proxy or tunnel. Slack
will send URL-verification and event callbacks to that URL.

## Behavior

- Verifies `X-Slack-Signature` and `X-Slack-Request-Timestamp` before accepting
  any request.
- Handles app mentions, plain message events, and slash command form payloads.
- Ignores bot messages.
- Replies into the originating channel and thread when Slack provides a thread
  timestamp.
- Supports plain text output and numbered clarify choices. File uploads and rich
  Block Kit approvals are not implemented yet.

## Scheduled Delivery

```yaml
delivery:
  mode: slack
  target: C0123456789
```

If `allowed_channels` is configured, the target must be listed there.

## Security

Do not disable Slack signature checks. Keep the signing secret and bot token in
environment variables, and restrict `allowed_teams`, `allowed_channels`, or
`allowed_users` for production bots.
