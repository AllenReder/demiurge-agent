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

Gateway mode starts enabled external channels for the selected core. It errors
when none are enabled.

Current external channel implementation supports Telegram. See
[telegram.md](telegram.md).

## Busy Behavior

Interactive channels can choose how to handle input while a turn is running:

- `interrupt`: new input interrupts current work.
- `queue`: new input is queued.

Initial behavior comes from host config `channel.busy_mode`. TUI can change the
current process with `/busy`.

## Delivery Semantics

Authored modules emit typed delivery requests. The host applies history policy,
registers artifacts, records events, and routes output to the current channel.

See [../reference/history-policy-and-delivery.md](../reference/history-policy-and-delivery.md).

## Success Check

```bash
uv run demiurge --provider fake
```

Then run `/status` and `/events`. For Telegram, run the gateway and send a
message from an allowed user.

## Boundary

Agent modules should not call TUI, Telegram, or other channel SDKs directly.
Use `ctx.input` and `ctx.output` delivery methods.
