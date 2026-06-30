# Troubleshooting

## Startup Uses the Fake Provider

Check `/status` for provider and API key sources. With `provider=auto`, demiurge
uses the fake provider when no real API key or provider configuration is
resolved.

## OpenAI-Compatible Provider Reports Missing API Key

Set:

```bash
export DEMIURGE_API_KEY="..."
```

Or pass a temporary CLI override:

```bash
uv run demiurge --provider openai --api-key "$DEMIURGE_API_KEY"
```

## File Tools Cannot Access a Path

File and terminal tools are scoped to the resolved workspace. In the local TUI,
that is the launch directory unless you override it:

```bash
uv run demiurge --workspace /path/to/project
```

Paths outside the workspace are rejected before tool execution.

## Writes, Deletes, or Terminal Commands Are Rejected

These effects need both an agent capability and approval. Non-interactive runs
fail closed when no approval provider is available.

## A Tool Exists in Source but the Model Sees `not allowed`

The runtime core may be behind the source template. Check drift:

```bash
uv run demiurge doctor
uv run demiurge init --check
```

If the runtime core is missing toolsets or slots and you want to refresh it,
back up then run:

```bash
uv run demiurge init --refresh assistant
```

The TUI `/doctor` command shows the same diagnostics.

## Telegram Does Not Respond

Check that:

- the current core has `channels.telegram.enabled: true`;
- `channels.telegram.bot_token_env` points to an environment variable with a
  value, or `bot_token` is set;
- the sender/chat numeric IDs are in the current core allowlists;
- group messages use `/ask`, `@bot` mention, reply-to-bot, or a slash command
  addressed to the bot;
- the host can reach the Telegram Bot API.

In private chat, `/status` can show session, running state, busy mode, and
queue count.
