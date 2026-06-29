# Channels

Channels normalize external input into host `InteractionBridge` input and deliver typed output back to the target platform. The host still owns sessions, turns, steps, context assembly, provider calls, tools, slash commands, `clarify`, approval, and delivery semantics.

Agent cores declare external channel configuration in `agent.yaml`. `demiurge gateway` starts enabled adapters from the selected core:

```yaml
channels:
  telegram:
    enabled: true
```

Channels are not slots. The host does not scan `agent/channels/`.

## TUI

The default local entry is the TUI:

```bash
uv run demiurge
```

The TUI uses a local session and supports slash commands, approval/question panels, tool display levels, session resume, trace/events, and in-flight input control.

The frontend is a TypeScript/Ink/React app in `ui-tui/`. Python owns the `TuiInteractionBridge`, session runner, provider calls, slash command dispatch, typed delivery, `clarify`, and approval.

Wheels include the built JS asset. Source development or release builds should refresh it when `ui-tui/` changes:

```bash
cd ui-tui
npm ci
npm run build
```

The TUI requires Node.js 20+ to run the bundled Ink frontend. Telegram does not depend on the TUI frontend.

Useful startup flags:

```bash
uv run demiurge --tool-display full
uv run demiurge --tool-display quiet
```

Runtime commands:

```text
/tool-display quiet
/tool-display summary
/tool-display full
/busy queue
/busy interrupt
/interrupt
```

`channel.busy_mode` initializes in-flight input behavior:

- `interrupt`: new ordinary input cancels the current turn and starts a new one.
- `queue`: new ordinary input waits until the current turn finishes.

The TUI process also starts the current core scheduler. `delivery.mode: local` scheduled runs write a fresh session and scheduler logs, but do not inject output into the current TUI transcript.

## Telegram

Telegram uses long polling and does not require a web server.

Core config:

```yaml
channels:
  telegram:
    enabled: true
    bot_token_env: DEMIURGE_TELEGRAM_BOT_TOKEN
    bot_token: null
    bot_username: your_bot
    allowed_users:
      - 123456789
    allowed_chats:
      - -1001234567890
    unauthorized_response: brief
    poll_timeout: 30
    message_format: markdown_v2
    register_commands: true
    send_typing: true
    rich_messages: true
    reply_to_mode: "off"
```

Run the gateway:

```bash
export DEMIURGE_TELEGRAM_BOT_TOKEN="..."
uv run demiurge gateway --core assistant
```

`demiurge gateway` starts all enabled external channels for the current core. `uv run demiurge` starts the local TUI and ignores `channels.*.enabled`.

`bot_token_env` wins when the environment variable is set. `bot_token` is a plaintext fallback and is not recommended for real tokens. `bot_username` is used for group mention detection and can be omitted.

## Telegram Authorization

Telegram access control uses numeric IDs only.

- Without `allowed_users` / `allowed_chats`, all Telegram messages and callbacks are rejected.
- Private chats require sender `from.id` in `allowed_users`.
- Groups and supergroups require both `chat.id` in `allowed_chats` and sender `from.id` in `allowed_users`.
- Group messages still need `/ask`, an `@bot` mention, a reply to the bot, or a Telegram slash command addressed to the bot.
- Unauthorized input does not create a session, enter a queue, consume `clarify`, or resolve approvals.
- `unauthorized_response: brief` sends a short rejection; `silent` ignores unauthorized input.

## Telegram Output

Tool display follows `ui.tool_display` / `--tool-display`:

- `quiet`: no tool call messages.
- `summary`: compact summaries.
- `full`: parameters, results, and `model_output`.

Output formatting:

- tables, task lists, `<details>`, and block math first try Telegram rich messages;
- ordinary Markdown uses MarkdownV2;
- if rich or MarkdownV2 fails, output falls back to plain text;
- GitHub pipe tables are rewritten into readable row groups when MarkdownV2 cannot represent them;
- long messages are split on Telegram's UTF-16 4096 limit while preserving code fences when possible.

Media delivery:

- image/video/file blocks try Telegram native photo/video/document APIs;
- audio blocks are converted to OGG/OPUS with `ffmpeg` and sent as voice messages;
- failures degrade to text artifact references.

## Clarify and Approval

`clarify` choices are sent as numbered options and inline buttons. Users can also reply with the number.

Approval-required tool calls in private chats send a structured MarkdownV2 message with:

- tool name;
- risk;
- target;
- command and parameter summary;
- `Allow once`, `Allow for session`, and `Deny` buttons.

The current turn pauses until approval resolves or times out. A 10-minute timeout denies the request. `/stop` cancels the current turn, clears queued work, and invalidates pending approval callbacks.

Telegram group chats do not support interactive approvals in v1. Approval-required actions fail closed and ask the user to retry in a private chat.

## Reply Anchoring

`reply_to_mode` controls Telegram reply references:

- `"off"`: no reply references, default;
- `"first"`: only the first message or first chunk replies;
- `"all"`: every outbound message replies.

This is only a Telegram UX option. It does not affect session routing or context assembly.

## Busy Queue

Telegram in-flight behavior also starts from host config `channel.busy_mode`:

- `interrupt`: ordinary new messages cancel the current turn.
- `queue`: ordinary new messages run FIFO after the current turn.

`/queue <prompt>` always queues explicitly. `/busy interrupt|queue` switches the current process only.

Background output delivery does not occupy the conversation busy state.

## Scheduler Delivery

Telegram gateway processes start the current core scheduler. A schedule with `delivery.mode: telegram` must declare `chat_id`, and that chat id must be allowed by the current core's Telegram config.

Scheduled Telegram delivery reuses `TelegramInteractionBridge.deliver()`. It does not participate in the Telegram busy queue and does not create user-input callbacks.

## Delivery Semantics

`delivery` is a host SDK concept, not a Telegram-specific feature. TUI and Telegram share the same semantics:

- `delivery="immediate"` queues the item as soon as it is generated;
- `delivery="slot_end"` queues the item after the current slot succeeds;
- delivery failure does not roll back session history or fail the turn;
- the host writes `delivery.failed` events for diagnostics.

Final outbound only returns pending items that have not already been dispatched, which avoids duplicate sends.
