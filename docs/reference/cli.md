# CLI Reference

## Main TUI Command

```bash
uv run demiurge [options]
```

Common options:

- `--home PATH`
- `--core CORE_ID`
- `--agents-root PATH`
- `--provider auto|fake|openai|openai-compatible`
- `--model MODEL`
- `--base-url URL`
- `--api-key KEY`
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
uv run demiurge package --help
uv run demiurge gateway --help
```
