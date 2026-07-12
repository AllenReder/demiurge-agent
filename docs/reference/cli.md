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

Checks runtime/source template drift. It also reports runtime core repository
consistency problems, such as a missing live ref, a live checkout that no
longer matches `refs/demiurge/live`, or a rollback ref that needs repair.

`doctor` and `init --check` use the same process exit contract:

| Exit code | Meaning |
| --- | --- |
| `0` | The report is healthy (`ok: true` in JSON mode). |
| `1` | The check completed and found one or more errors (`ok: false`). |
| `2` | Arguments, configuration, or the check itself could not be processed. |

JSON reports remain on stdout and machine-readable for both healthy and
unhealthy results. In JSON mode, an execution failure also emits an `ok: false`
payload with `error.code: doctor.execution_error`, without a traceback or raw
configuration values. Callers must inspect the process status as well as the
JSON payload. In particular, the managed `update` health gate now stops when
`init --check` reports an unhealthy runtime.

## `core`

```bash
uv run demiurge core status
uv run demiurge core versions
uv run demiurge core check
uv run demiurge core save
uv run demiurge core diff
uv run demiurge core discard --yes
uv run demiurge core evolve start Improve concise replies
uv run demiurge core evolve review <run_id>
uv run demiurge core evolve promote <run_id> --manual-review-token <token>
uv run demiurge core evolve discard <run_id>
uv run demiurge core rollback
uv run demiurge core rollback <revision>
```

Inspects and mutates the Git-backed runtime agents tree. Revisions are commits
in `~/.demiurge/.core.git`. `core status` includes a repository consistency
section when the live ref, previous ref, or checkout state needs manual repair.

`core check` runs host-owned gates against the live agents tree. `core evolve
start` creates an isolated worktree under `.evolve/runs/<run_id>/agents`.
Review records `refs/demiurge/runs/<run_id>`, promote advances
`refs/demiurge/live`, and rollback creates a new rollback commit. Promotion
rejects stale evolve proposals whose recorded base revision no longer matches
the current live revision. If review reports an MCP declaration security diff,
it also prints a content-bound `mcp-review:<sha256>` token. Pass that exact token
to `core evolve promote --manual-review-token`; a missing or stale token leaves
the Git refs unchanged. Runs without that security diff do not require the
option.

`core diff` shows local agent edits in `~/.demiurge/agents` without writing
files. `core save` validates those edits and commits them as a new
`core_revision`. `core discard --yes` resets the live checkout to
`refs/demiurge/live` and removes untracked local agent edits.

Run/edit workflows save local agent edits automatically before loading the live
core. Read-only commands do not create commits. Switching workflows such as
`core evolve promote` and `core rollback` refuse to continue when unsaved local
agent edits remain; save or discard them first.

## `setup`

```bash
uv run demiurge setup status
uv run demiurge setup providers list
uv run demiurge setup providers add openai --preset openai --set-default
uv run demiurge setup providers edit openai --base-url https://proxy.example.test/v1
uv run demiurge setup providers add local-anthropic --api-mode anthropic-messages --base-url https://llm.example.test/v1
uv run demiurge setup providers remove local-anthropic
uv run demiurge setup providers set-default openai
uv run demiurge setup providers test openai --model <model-name>
uv run demiurge setup model set --core assistant --provider openai --model <model-name>
uv run demiurge setup timezone set Asia/Shanghai
uv run demiurge setup timezone clear
```

Provider presets currently include:

```text
anthropic, dashscope, deepseek, minimax, minimax-cn, moonshot, openai,
openrouter, siliconflow, zai
```

## `package`

```bash
uv run demiurge package list --core assistant
uv run demiurge package list --repo builtin
uv run demiurge package install <package_id|repo/package_id> --core assistant
uv run demiurge package install <package_id|repo/package_id> --core assistant --preview
uv run demiurge package install <package_id|repo/package_id> --core assistant --option key=value
uv run demiurge package uninstall <package_id|repo/package_id> --core assistant
uv run demiurge package uninstall <package_id|repo/package_id> --core assistant --force-drift
uv run demiurge package repo list
uv run demiurge package repo add ./local-packages --alias local --trust
uv run demiurge package repo add https://github.com/user/demiurge-packages.git --alias community --ref main --trust
uv run demiurge package repo sync community
uv run demiurge package repo remove community
```

External path and git repositories must be trusted before they can install local
Agent Slot code.

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
uv run demiurge core --help
uv run demiurge setup --help
uv run demiurge package --help
uv run demiurge gateway --help
```
