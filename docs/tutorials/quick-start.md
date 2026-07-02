---
title: Quick Start
description: Start the Demiurge TUI locally with the fake provider and no API key.
---

# Quick Start

This tutorial starts the Demiurge TUI with the fake provider. It does not require
an API key, so it is the safest first run.

You will finish with a running TUI, a visible `/status` report, and links to the
next setup tasks.

## Before You Start

Install:

- `git`
- `uv`
- Node.js 20 or newer

The managed install path is best for normal use. The source checkout path is for
working on Demiurge itself.

## 1. Choose an Install Path

For a managed install, run this from a checkout of the Demiurge repository:

```bash
scripts/install.sh
```

The installer requires `git` and `uv`, creates or reuses the managed checkout at
`~/.demiurge/demiurge-agent`, runs `uv sync`, and initializes the runtime home.
The command path is:

```bash
~/.demiurge/demiurge-agent/.venv/bin/demiurge
```

For source checkout development, run:

```bash
uv sync --all-groups
uv run demiurge init
```

Then use `uv run demiurge` for the commands below.

## 2. Start the TUI

Managed install:

```bash
~/.demiurge/demiurge-agent/.venv/bin/demiurge --provider fake
```

Source checkout:

```bash
uv run demiurge --provider fake
```

Running `demiurge` without a subcommand starts the TUI. The `--provider fake`
override keeps the first run independent of provider setup.

## 3. Confirm the Runtime

Inside the TUI, run:

```text
/status
/exit
```

`/status` should show the selected core, runtime home, workspace, provider, and
session path.

If the workspace is not the project you expected, restart from that directory or
pass `--workspace /path/to/project`.

## 4. Know the Command Surface

The top-level subcommands are:

- `init`
- `doctor`
- `package`
- `update`
- `setup`
- `gateway`

Run `demiurge setup` without another subcommand to open the setup wizard.

## 5. Continue

Choose the next task:

- Configure a real model provider with [Configure a Provider](../how-to/configure-provider.md).
- Pick the right file and terminal scope with [Choose a Workspace](../how-to/choose-workspace.md).
- Install reusable capabilities with [Install Packages](../how-to/install-packages.md).
- Change the runtime Agent Core with [Customize an Agent Core](customize-agent-core.md).
- Diagnose startup issues with [Troubleshoot](../how-to/troubleshoot.md).

## If Startup Fails

Run the read-only checks:

```bash
uv run demiurge init --check
uv run demiurge doctor
```

For a managed install, replace `uv run demiurge` with:

```bash
~/.demiurge/demiurge-agent/.venv/bin/demiurge
```
