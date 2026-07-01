---
title: CLI Reference
description: Command and option reference for the Demiurge CLI.
---

# CLI Reference

All Python commands should be run through `uv` from a source checkout unless you
are using a managed installed binary.

## Main TUI Command

```bash
uv run demiurge [options]
```

Common options:

| Option | Meaning |
| --- | --- |
| `--home HOME` | Runtime home directory. |
| `--core CORE` | Core id to run. |
| `--agents-root AGENTS_ROOT` | Source agents root override. |
| `--provider PROVIDER` | Provider profile id, `auto`, or `fake`. |
| `--model MODEL` | Model override. |
| `--fake-script FAKE_SCRIPT` | Fake provider script JSON. |
| `--workspace WORKSPACE` | Workspace root for file and terminal tools. |
| `--timezone TIMEZONE` | Runtime IANA timezone override. |
| `--session SESSION` | Session id to create or resume. |
| `--resume RESUME` | Existing session id to resume. |
| `--tool-display quiet|summary|full` | TUI tool call display level. |

## `init`

```bash
uv run demiurge init
uv run demiurge init --check
uv run demiurge init --json
uv run demiurge init --refresh assistant
uv run demiurge init --refresh all
```

Initializes or refreshes runtime templates under the runtime home. `--check` is
read-only.

## `doctor`

```bash
uv run demiurge doctor
uv run demiurge doctor --core assistant
uv run demiurge doctor --json
```

Checks runtime/source template drift.

## `setup`

```bash
uv run demiurge setup status
uv run demiurge setup providers list
uv run demiurge setup providers add openai --preset openai --set-default
uv run demiurge setup providers edit openai --base-url https://api.openai.com/v1
uv run demiurge setup providers remove openai
uv run demiurge setup providers set-default openai
uv run demiurge setup providers test openai --model gpt-5.5
uv run demiurge setup model set --core assistant --provider openai --model gpt-5.5
uv run demiurge setup timezone set Asia/Shanghai
uv run demiurge setup timezone clear
```

Provider presets currently include:

```text
dashscope, deepseek, minimax, minimax-cn, moonshot, openai, openrouter,
siliconflow, zai
```

## `package`

```bash
uv run demiurge package list --core assistant
uv run demiurge package list --repo builtin
uv run demiurge package install <package_id|repo/package_id> --core assistant
uv run demiurge package install <package_id|repo/package_id> --core assistant --preview
uv run demiurge package install <package_id|repo/package_id> --core assistant --option key=value
uv run demiurge package uninstall <package_id|repo/package_id> --core assistant
uv run demiurge package repo list
uv run demiurge package repo add ./local-packages --alias local --trust
uv run demiurge package repo add https://github.com/user/demiurge-packages.git --alias community --ref main --trust
uv run demiurge package repo sync community
uv run demiurge package repo remove community
```

External path and git repositories must be trusted before they can install local
code slots.

## `update`

```bash
demiurge update
demiurge update --ref v0.4.0
demiurge update --skip-init-check
```

Updates a managed checkout and optionally runs a read-only runtime drift check.

## `gateway`

```bash
uv run demiurge gateway --core assistant
uv run demiurge gateway --core assistant --provider fake
uv run demiurge gateway --core assistant --timezone Asia/Shanghai
```

Runs enabled external channels for the selected core.

## Verification Commands

Use these after documentation or CLI-surface changes:

```bash
uv run demiurge --help
uv run demiurge init --help
uv run demiurge setup --help
uv run demiurge package --help
uv run demiurge gateway --help
```
