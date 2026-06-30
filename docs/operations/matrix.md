# Matrix Channel

The Matrix channel uses the Matrix Client-Server REST API. It long-polls `/sync`
for text messages and sends `m.room.message` text replies.

End-to-end encrypted rooms are not supported.

## Configure

```yaml
channels:
  matrix:
    enabled: true
    homeserver_url: https://matrix.example.org
    access_token_env: MATRIX_ACCESS_TOKEN
    user_id: "@demiurge:example.org"
    allowed_rooms:
      - "!room:example.org"
    poll_timeout: 30
```

## Behavior

- Polls `/sync` with the configured timeout.
- Processes `m.room.message` events with `msgtype: m.text` or `m.notice`.
- Ignores messages sent by `user_id`.
- Sends plain text room messages.

The sync token is kept in memory for the running gateway process. Restarting the
gateway can cause Matrix to replay recent events depending on the homeserver.

## Scheduled Delivery

```yaml
delivery:
  mode: matrix
  target: "!room:example.org"
```

If `allowed_rooms` is configured, the target must be listed there.
