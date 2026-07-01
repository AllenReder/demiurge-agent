---
title: Quick Start
description: Start Demiurge locally with the fake provider, then choose the next setup step.
---

# Quick Start

This is the shortest path to a running Demiurge TUI. It uses the fake provider,
so you do not need an API key yet.

After the TUI opens, configure a real provider or install packages from the
links at the end.

## 1. Choose How You Run Demiurge

For a managed user install, run:

```bash
scripts/install.sh
```

The installer prints the managed command path. By default it is:

```bash
~/.demiurge/demiurge-agent/.venv/bin/demiurge
```

For source checkout development, run:

```bash
uv sync --all-groups
```

Then use `uv run demiurge` for the commands below.

## 2. Initialize Once

Managed install:

```bash
~/.demiurge/demiurge-agent/.venv/bin/demiurge init
```

Source checkout:

```bash
uv run demiurge init
```

## 3. Start the TUI

Managed install:

```bash
~/.demiurge/demiurge-agent/.venv/bin/demiurge --provider fake
```

Source checkout:

```bash
uv run demiurge --provider fake
```

The TUI should open without requiring any provider secrets.

## 4. Confirm It Works

Inside the TUI, run:

```text
/status
/exit
```

`/status` should show the selected core, runtime home, workspace, provider, and
session path.

## 5. Next Steps

Choose the next task:

- Configure a real model provider with [Configure a Provider](../how-to/configure-provider.md).
- Install reusable capabilities with [Install Packages](../how-to/install-packages.md).
- Change the runtime Agent Core with [Customize an Agent Core](customize-agent-core.md).
- Diagnose startup issues with [Troubleshoot](../how-to/troubleshoot.md).

## Useful Checks

If startup fails, run:

```bash
uv run demiurge init --check
uv run demiurge doctor
```

For a managed install, replace `uv run demiurge` with:

```bash
~/.demiurge/demiurge-agent/.venv/bin/demiurge
```
