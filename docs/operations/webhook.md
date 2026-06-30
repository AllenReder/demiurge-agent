# Webhook Channel

The webhook channel exposes a small local HTTP JSON endpoint and forwards input
to Demiurge's interaction runtime. It is useful for custom UIs, internal
services, Home Assistant automations, or provider-specific adapters you do not
want to build into Demiurge yet.

## Configure

```yaml
channels:
  webhook:
    enabled: true
    host: 127.0.0.1
    port: 8765
    path: /demiurge
    token_env: DEMIURGE_WEBHOOK_TOKEN
    callback_url_env: DEMIURGE_WEBHOOK_CALLBACK_URL
    allowed_sources: []
```

`token_env` is required unless `allow_unauthenticated: true` is set for a trusted
local-only deployment.

## Inbound Request

Send `POST` JSON to the configured path:

```json
{
  "text": "hello",
  "source": "alice",
  "conversation_key": "webhook:alice",
  "request_id": "req-123",
  "callback_url": "https://example.com/demiurge/callback",
  "metadata": {}
}
```

Authenticate with either:

- `Authorization: Bearer <token>`
- `X-Demiurge-Token: <token>`
- a `token` JSON field

Demiurge returns `202` after accepting the turn. Output is posted to the request
`callback_url`, the configured `callback_url`, or a named `delivery_targets`
entry.

## Scheduled Delivery

```yaml
delivery:
  mode: webhook
  target: ops
```

The target must exist in `channels.webhook.delivery_targets`.

## Security

Callback URLs are SSRF-sensitive. By default Demiurge requires HTTPS callback
URLs and rejects private, loopback, link-local, multicast, reserved, unresolved,
and redirecting callback targets. Only set `allow_private_callback_urls: true`
for trusted local integrations.
