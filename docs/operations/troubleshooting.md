# Troubleshooting

## Startup Uses the Fake Provider

`--provider auto` falls back to fake defaults when no real provider values are
resolved. Set `DEMIURGE_MODEL_NAME`, `DEMIURGE_BASE_URL`, and
`DEMIURGE_API_KEY`, or pass CLI overrides.

## Missing API Key

Use environment variables:

```bash
export DEMIURGE_API_KEY="..."
uv run demiurge --provider openai
```

`/status` prints the key source, not the key value.

## File Tools Cannot Access a Path

File and terminal tools are scoped to the resolved workspace. Start from the
project directory or pass:

```bash
uv run demiurge --workspace /path/to/project
```

External channel runs use the selected core's `runtime.workspace`, then
`~/.demiurge/workspace`.

## Writes or Terminal Commands Are Rejected

Approval-required actions need an interaction bridge. In non-interactive runs,
approval fails closed unless policy can safely auto-approve.

Terminal commands also pass through hardline blocks that approval config cannot
bypass.

## A Tool Exists but the Model Cannot Use It

Check:

- the core enables the relevant built-in toolset;
- authored tool `slot.yaml` loaded correctly;
- required capabilities are declared;
- approval policy does not deny the tool;
- MCP server discovery succeeded.

Use:

```text
/tools
/events
```

## Telegram Does Not Respond

Check:

- gateway was started with `uv run demiurge gateway --core <core>`;
- `channels.telegram.enabled` is true in the selected concrete core;
- `DEMIURGE_TELEGRAM_BOT_TOKEN` is set if `bot_token_env` is used;
- sender `from.id` is in `allowed_users`;
- groups also include `chat.id` in `allowed_chats`.

## Runtime Drift

Check without writing files:

```bash
uv run demiurge init --check
uv run demiurge doctor
```

Use explicit refresh only when you intend to replace runtime templates.
