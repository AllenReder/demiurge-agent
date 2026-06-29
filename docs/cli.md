# CLI Reference

## TUI

```bash
uv run demiurge [options]
```

Common options:

- `--home PATH`
- `--core NAME`
- `--agents-root PATH`
- `--provider auto|fake|openai|openai-compatible`
- `--model NAME`
- `--base-url URL`
- `--api-key KEY`
- `--fake-script PATH`
- `--workspace PATH`
- `--session SESSION_ID`
- `--resume SESSION_ID`
- `--tool-display quiet|summary|full`

`--tool-display` controls tool rendering for TUI and Telegram:

- `quiet`: show final assistant messages only.
- `summary`: default, show compact tool status and truncated summaries.
- `full`: show tool arguments, full results, and `model_output`.

TUI can switch at runtime with `/tool-display quiet|summary|full`.

When `--core` is omitted, demiurge reads `<home>/config.yaml`
`runtime.default_core`, defaulting to `assistant`. When `--workspace` and
`DEMIURGE_WORKSPACE` are omitted, demiurge reads `runtime.workspace`; if it is
null, `<home>/workspace` is used.

The TUI is a TypeScript/Ink/React frontend plus a Python channel adapter.
`uv run demiurge` starts the frontend, which connects to
`python -m demiurge.ui_gateway.entry` through stdio JSON-RPC. The Python
`TuiInteractionBridge` implements the same host interaction boundary used by
Telegram and future channels.

Wheels include the built JS asset. Source development or release builds need:

```bash
cd ui-tui
npm ci
npm run build
```

Common TUI slash commands:

- `/help`
- `/status`
- `/core` / `/versions`
- `/doctor`
- `/tools`
- `/skills [category]`
- `/skill <name> [file_path]`
- `/packages [package|install <package>|uninstall <package>]`
- `/sessions`
- `/resume [session_id|number]`
- `/new`
- `/compact [focus]`
- `/last` / `/trace [turn_id|last]`
- `/events [type] [limit]`
- `/busy interrupt|queue`
- `/interrupt`
- `/evolve <goal>` / `/rollback [version]`
- `/provider`
- `/exit` / `/quit`

`/busy interrupt` is the default in-flight input behavior. New messages cancel
the current turn and start a new one. `/busy queue` queues new messages until
the current turn finishes.

`--resume` requires an existing session. `--session` uses a fixed session id,
resuming it if present or creating it if missing.

## init

```bash
uv run demiurge init
uv run demiurge init --json
```

Creates missing host config and refreshes runtime fallback, assistant, and
evolver templates after backup. Existing `config.yaml` is not overwritten.
`--core NAME` selects the target core; the runtime evolver is still filled in.

Read-only drift check:

```bash
uv run demiurge init --check
uv run demiurge init --check --json
```

Refresh selected runtime templates:

```bash
uv run demiurge init --refresh assistant
uv run demiurge init --refresh evolver
uv run demiurge init --refresh global
uv run demiurge init --refresh all
```

`init --refresh` does not refresh `config.yaml`.

## update

```bash
uv run demiurge update
uv run demiurge update --install-dir ~/.demiurge/demiurge-agent
uv run demiurge update --ref main
```

`update` is for managed checkout installs. The default checkout is
`<home>/demiurge-agent`, which is `~/.demiurge/demiurge-agent` with the default
home.

It runs:

1. `git fetch --all --prune`
2. `git pull --ff-only`, or `git checkout <ref>` when `--ref` is provided
3. `uv sync`
4. `uv run demiurge init --home <home> --check`

The final step is read-only. It does not refresh or overwrite live cores under
`~/.demiurge/agents`. Use `demiurge init --refresh ...` for explicit template
refresh.

Options:

- `--home PATH`: runtime home.
- `--install-dir PATH`: managed checkout directory.
- `--ref REF`: branch, tag, or commit to checkout. When provided, no forced
  `git pull` is run.
- `--skip-init-check`: skip read-only runtime/source drift check.

## doctor

```bash
uv run demiurge doctor
uv run demiurge doctor --json
```

`doctor` checks runtime home, global fallback, assistant/evolver cores, source
template drift, common missing tools, and provider environment variables. It is
read-only.

## package

```bash
uv run demiurge package
uv run demiurge package list
uv run demiurge package list --core assistant
uv run demiurge package list --tag tts --json
uv run demiurge package install minimax_tts --core assistant
uv run demiurge package install minimax_tts --core assistant --option mode=summary
uv run demiurge package install minimax_tts --core assistant --preview
uv run demiurge package uninstall minimax_tts --core assistant
uv run demiurge package uninstall minimax_tts --core assistant --preview
```

`package` manages catalog packages installed into runtime active cores. Without a
subcommand, it starts the interactive wizard. Scripted `install` and
`uninstall` require `--core`. `install --option KEY=VALUE` may be repeated for
package options. `--preview` shows the planned changes without writing files.

`--catalog-root PATH` can point to another catalog with the same layout as
`agent-catalog/`.

## gateway

```bash
uv run demiurge gateway --core assistant
```

`gateway` does not start the TUI. It reads external channel config from the
current core and runs enabled channel bridges. The current external channel
implementation supports Telegram.

Telegram must be enabled in the core:

```yaml
channels:
  telegram:
    enabled: true
    bot_token_env: DEMIURGE_TELEGRAM_BOT_TOKEN
```

Token resolution prefers the environment variable named by `bot_token_env`, then
falls back to `bot_token`. Missing token or unknown external channels fail
startup.

Telegram supports BotCommand registration, `/help`, `/status`, `/new`, `/stop`,
`/queue`, `/busy interrupt|queue`, `/sessions`, `/resume`, `/tools`, `/skills`,
and `/skill`. Output falls back from rich messages to MarkdownV2 to plain text
when needed. `clarify` choices are sent as numbers and inline buttons.
