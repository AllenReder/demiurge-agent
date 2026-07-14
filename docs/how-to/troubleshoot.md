---
title: Troubleshoot
description: Diagnose common Demiurge startup, configuration, package, and channel failures.
---

# Troubleshoot

Start with the exact command, exact error text, and whether you are using the
managed checkout or a source checkout. Most onboarding failures are caused by
Node.js version problems, runtime-home drift, missing provider secrets, invalid
YAML, or an unexpected workspace.

For a managed install, replace `uv run demiurge` with:

```bash
~/.demiurge/demiurge-agent/.venv/bin/demiurge
```

## Confirm the Command Surface

Running `demiurge` without a subcommand starts the TUI. The top-level
subcommands are:

- `init`
- `doctor`
- `package`
- `update`
- `setup`
- `gateway`

Run `demiurge setup` without another setup subcommand to open the setup wizard.

## TUI Does Not Start

The TUI requires Node.js 20 or newer:

```bash
node --version
```

If Node is missing or too old, install Node.js 20 or newer and retry:

```bash
uv run demiurge --provider fake
```

## Command Not Found

For a managed install, use the managed command path:

```bash
~/.demiurge/demiurge-agent/.venv/bin/demiurge --provider fake
```

For a source checkout, run commands through `uv` from the repository:

```bash
uv run demiurge --provider fake
```

## Runtime Drift or Missing Runtime Files

Check without writing files:

```bash
uv run demiurge init --check
uv run demiurge doctor
```

Refresh templates only after reviewing the drift and deciding to update runtime
files:

```bash
uv run demiurge init --refresh assistant
```

Use `init --refresh global` only for the global fallback config, and use
`init --refresh all` only when you intend to refresh all runtime templates.

## Runtime Permission Check Fails

`demiurge doctor` is read-only. On POSIX it reports
`runtime.permissions.insecure` when the runtime home, `.env`, `config.yaml`,
SQLite files, logs, state, or artifacts do not satisfy the Host `0700`/`0600`
policy.

Stop running Demiurge processes, review every path in the finding, and check
for unexpected symbolic links or ownership changes. A normal mutating startup
or init tightens existing modes without rewriting file contents:

```bash
uv run demiurge init
uv run demiurge doctor
```

If init cannot tighten a path, fix its owner/permissions outside Demiurge and
retry. Do not replace a listed runtime path with a symlink; private write paths
reject symlinks. Windows uses platform ACL semantics, so numeric POSIX mode
findings are not emitted there.

## Provider or API Key Fails

Inspect setup state:

```bash
uv run demiurge setup status
```

Use the fake provider to separate runtime issues from live provider issues:

```bash
uv run demiurge --provider fake
```

If `fake` works, check:

- The selected provider profile exists.
- The provider profile has a base URL.
- The configured `api_key_env` is exported or written to `~/.demiurge/.env`.
- The selected core model uses the intended provider and `<model-name>`.

Provider resolution order is CLI override, core manifest, global fallback, host
default, then `fake`.

## Core or Slot Does Not Load

Run:

```bash
uv run demiurge init --check
```

Then check the affected files:

- `agent.yaml`
- `agent/pipelines.yaml`
- the slot `slot.yaml`
- the slot `module.py`
- authored tool `tool.yaml` when a tool fails to load

Compare with [../reference/contracts/slot-modules.md](../reference/contracts/slot-modules.md).

## Workspace Is Wrong or Tools Are Rejected

Inside the TUI, run:

```text
/status
```

Workspace resolution order is `--workspace`, `DEMIURGE_WORKSPACE`, TUI launch
directory, core `runtime.workspace`, then `~/.demiurge/workspace`.

Run with an explicit workspace:

```bash
uv run demiurge --workspace /path/to/project --provider fake
```

Approvals and sensitive-path checks still apply inside the workspace.

## Package Install Fails

Preview first:

```bash
uv run demiurge package install <package_id> --core assistant --preview
```

Check that the repository has:

```text
repository.yaml
packages/<package_id>.yaml
```

External repositories must be trusted before they can install local Agent Slot
code.

## Telegram Does Not Respond

Check:

- `channels.telegram.enabled: true`
- `DEMIURGE_TELEGRAM_BOT_TOKEN` is set
- `allowed_users` or `allowed_chats` includes the caller
- the gateway is running with the intended core

```bash
uv run demiurge gateway --core assistant --provider fake
```

## Manual Links Break

Build the site:

```bash
cd website
npm run build
```

Docusaurus is configured to throw on broken regular links and warn on broken
Markdown links.
