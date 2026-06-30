# CLI Reference

## Main TUI Command

```bash
uv run demiurge [options]
```

Common options:

- `--home PATH`
- `--core CORE_ID`
- `--agents-root PATH`
- `--provider PROFILE_ID|auto|fake`
- `--model MODEL`
- `--fake-script PATH`
- `--workspace PATH`
- `--session SESSION_ID`
- `--resume SESSION_ID`
- `--tool-display quiet|summary|full`

## `init`

```bash
uv run demiurge init
uv run demiurge init --check
uv run demiurge init --refresh assistant
uv run demiurge init --refresh all
```

Initializes or refreshes runtime templates under the runtime home. `--check` is
read-only.

## `doctor`

```bash
uv run demiurge doctor
uv run demiurge doctor --json
```

Checks runtime/source template drift.

## `setup`

```bash
uv run demiurge setup
uv run demiurge setup status --json
uv run demiurge setup providers list --json
uv run demiurge setup providers add deepseek --preset deepseek --set-default
uv run demiurge setup providers edit deepseek --base-url https://api.deepseek.com
uv run demiurge setup providers remove deepseek
uv run demiurge setup providers set-default deepseek
uv run demiurge setup providers test deepseek --model deepseek-v4-flash
uv run demiurge setup model set --core assistant --provider deepseek --model deepseek-v4-flash
```

Configures host-owned provider profiles and core model defaults. Provider
secrets can live in `~/.demiurge/.env`, shell environment variables, or direct
host config values. JSON output redacts direct API keys.

## `package`

```bash
uv run demiurge package
uv run demiurge package list --core assistant
uv run demiurge package install <package_id> --core assistant --preview
uv run demiurge package uninstall <package_id> --core assistant
```

Manages catalog packages for runtime cores.

## `update`

```bash
demiurge update
demiurge update --ref v0.2.0
demiurge update --skip-init-check
```

Updates a managed checkout and optionally runs a read-only runtime drift check.

## `gateway`

```bash
uv run demiurge gateway --core assistant
```

Runs enabled external channels for the selected core. Supported external
channels are Telegram, generic webhook, Slack, Mattermost, Matrix, and email.

## Success Check

```bash
uv run demiurge --help
uv run demiurge init --help
uv run demiurge setup --help
uv run demiurge package --help
uv run demiurge gateway --help
```
