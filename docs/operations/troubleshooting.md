# Troubleshooting

## Startup Uses the Fake Provider

`--provider auto` falls back to fake defaults when no core provider or host
default provider is configured. Run:

```bash
uv run demiurge setup status --json
uv run demiurge setup providers list --json
```

Then set a default provider profile or pass `--provider <profile>` explicitly.

## Missing API Key

Check the selected profile's `api_key_env`:

```bash
uv run demiurge setup providers show deepseek --json
```

Set the key in the shell, `~/.demiurge/.env`, or a direct `api_key` field.
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
