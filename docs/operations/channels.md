# Channels

Channels adapt platform input and output. They do not own the model loop,
session storage, tool execution, or approvals; the host runner owns those.

## Local TUI

Start the TUI:

```bash
uv run demiurge --provider fake
```

The TUI uses the launch directory as the default workspace unless `--workspace`
or `DEMIURGE_WORKSPACE` is set.

When the selected provider supports response streaming, the TUI shows assistant
text incrementally for the default passthrough output path. This is an automatic
runner behavior, not a host config switch. Streaming is used only for channel
turns whose output pipeline is the default persistent `base_output` path; custom
output pipelines continue through the normal complete-response path. Tool-call
deltas are assembled by the host runner and still execute through the regular
tool runtime. If a stream fails after partial output reaches the TUI, Demiurge
marks the streamed part as cancelled and does not issue a second fallback model
request.

Useful commands:

- `/status`
- `/tools`
- `/sessions`
- `/resume`
- `/events`
- `/trace`
- `/compact`
- `/tool-display quiet|summary|full`
- `/busy interrupt|queue`
- `/interrupt`

## External Gateway

```bash
uv run demiurge gateway --core assistant
```

Gateway mode starts every enabled external channel for the selected core. It
errors when none are enabled.

Supported external channels:

| Channel | Transport | Notes |
| --- | --- | --- |
| Telegram | Bot API long polling | Richest support, including private-chat approvals and media delivery. |
| Webhook | Local HTTP JSON endpoint | Generic integration surface for custom UIs, automations, and services. |
| Slack | Events API / slash-command HTTP endpoint + Web API | Requires a public endpoint and Slack signing-secret verification. |
| Mattermost | Slash/outgoing webhook endpoint + REST or incoming webhook | Requires webhook-token validation. |
| Matrix | Client-Server REST `/sync` polling | Plain text only; encrypted rooms are not supported. |
| Email | IMAP polling + SMTP replies | Plain text only; attachments are ignored. |

Each channel is disabled by default. Enable channels in the concrete core's
`agent.yaml` under `channels`:

```yaml
channels:
  webhook:
    enabled: true
    token_env: DEMIURGE_WEBHOOK_TOKEN
    host: 127.0.0.1
    port: 8765
    path: /demiurge
```

Run the gateway after exporting the configured secrets:

```bash
export DEMIURGE_WEBHOOK_TOKEN="..."
uv run demiurge gateway --core assistant
```

HTTP webhook-based channels bind a local server. Use a reverse proxy or tunnel
outside Demiurge when a platform needs to reach the endpoint from the public
Internet. Put TLS and public ingress controls at that layer.

See also:

- [telegram.md](telegram.md)
- [webhook.md](webhook.md)
- [slack.md](slack.md)
- [mattermost.md](mattermost.md)
- [matrix.md](matrix.md)
- [email.md](email.md)

## Busy Behavior

Interactive channels can choose how to handle input while a turn is running:

- `interrupt`: new input interrupts current work.
- `queue`: new input is queued.

Initial behavior comes from host config `channel.busy_mode`. TUI can change the
current process with `/busy`. External text channels also support `/busy`,
`/status`, `/new`, `/stop`, `/queue`, `/sessions`, `/resume`, `/tools`,
`/skills`, and `/skill` where the platform can send text back.

## Scheduled Delivery

Schedules can deliver locally or to an enabled external channel. Telegram keeps
its compatibility field:

```yaml
delivery:
  mode: telegram
  chat_id: 123456789
```

Other channels use `target`:

```yaml
delivery:
  mode: matrix
  target: "!room:example.org"
```

The target must pass the channel allowlist/config validation. For example,
Matrix targets must be present in `allowed_rooms` when that list is configured,
and email targets must be present in `allowed_recipients` when configured.

## Delivery Semantics

Authored modules emit typed delivery requests. The host applies history policy,
registers artifacts, records events, and routes output to the current channel.

See [../reference/history-policy-and-delivery.md](../reference/history-policy-and-delivery.md).

## Security Defaults

- New channels are disabled until explicitly configured.
- Secrets should be read from environment variables.
- Webhook-style channels verify bearer tokens, HMAC signatures, or shared
  webhook tokens before accepting input.
- Generic webhook callback URLs must use HTTPS and reject private, loopback,
  link-local, multicast, reserved, unresolved, and redirect targets unless
  `allow_private_callback_urls: true` is explicitly set for a trusted local
  deployment.
- Telegram private chats support interactive approvals. Other external channels
  fail closed for approval-required actions until platform-specific private
  approval UX is implemented.

## Success Check

```bash
uv run demiurge --provider fake
```

Then run `/status` and `/events`. For external channels, run the gateway and send
a message from an allowed sender/source.

## Boundary

Agent modules should not call TUI, Telegram, Slack, Matrix, email, or other
channel SDKs directly. Use `ctx.input` and `ctx.output` delivery methods.
