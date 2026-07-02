---
title: Troubleshoot
description: Diagnose common Demiurge startup, configuration, package, and channel failures.
---

# Troubleshoot

Start with the exact command and exact error text. Most failures are caused by
runtime-home drift, missing secrets, invalid YAML, workspace scope, or channel
allowlist configuration.

## Runtime Drift

Check without writing files:

```bash
uv run demiurge init --check
uv run demiurge doctor
```

Refresh templates only when you intend to update runtime files:

```bash
uv run demiurge init --refresh assistant
```

## Missing Provider or API Key

Inspect setup state:

```bash
uv run demiurge setup status
```

Use the fake provider to separate runtime issues from provider issues:

```bash
uv run demiurge --provider fake
```

## Core Does Not Load

Run:

```bash
uv run demiurge init --check
```

Then check the affected files:

- `agent.yaml`
- `agent/slots.yaml`
- the slot `module.py`
- authored tool `tool.yaml` when a tool fails to load

Compare with [../reference/contracts/slot-modules.md](../reference/contracts/slot-modules.md).

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

## File or Terminal Tool Is Rejected

Check workspace:

```text
/status
```

Run with an explicit workspace:

```bash
uv run demiurge --workspace /path/to/project
```

Approvals and sensitive-path checks still apply.

## Telegram Does Not Respond

Check:

- `channels.telegram.enabled: true`
- `DEMIURGE_TELEGRAM_BOT_TOKEN` is set
- `allowed_users` or `allowed_chats` includes the caller
- the gateway is running with the intended core

```bash
uv run demiurge gateway --core assistant --provider fake
```

## Website or Manual Links Break

Build the site:

```bash
cd website
npm run build
```

Docusaurus is configured to throw on broken regular links and warn on broken
Markdown links.
