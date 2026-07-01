---
title: First Local Run
description: Install or sync Demiurge, initialize the runtime home, and start the TUI with the fake provider.
---

# First Local Run

This tutorial gets Demiurge running locally without an API key. It verifies the
host runtime, runtime home, source templates, TUI bridge, and session storage.

Use the fake provider first. Configure a real model only after this path works.

## 1. Install or Sync

For a managed user install:

```bash
scripts/install.sh
```

The managed checkout lives at:

```text
~/.demiurge/demiurge-agent
```

For source checkout development:

```bash
uv sync --all-groups
```

Confirm the command is available:

```bash
uv run demiurge --help
```

## 2. Initialize the Runtime Home

```bash
uv run demiurge init
```

This creates or refreshes the local runtime structure:

```text
~/.demiurge/
  config.yaml
  .env
  agents/
    agent.yaml
    assistant/
    evolver/
  workspace/
```

Check for template drift without writing files:

```bash
uv run demiurge init --check
uv run demiurge doctor
```

## 3. Start the TUI

```bash
uv run demiurge --provider fake
```

The default local interface is the TUI. It connects to the Python host over
stdio JSON-RPC. Wheels include the built TUI asset, so Node.js is only needed
when you edit `ui-tui/`.

Inside the TUI, run:

```text
/status
/tools
/sessions
/exit
```

`/status` should show the selected core, runtime home, workspace, provider,
model source, and session path.

## 4. Locate the Live Agent Core

Runtime Agent Cores live under:

```text
~/.demiurge/agents/<core_id>/
```

The default assistant core is:

```text
~/.demiurge/agents/assistant/
```

Do not edit repository source templates when you are experimenting with a live
agent. Edit the runtime core under `~/.demiurge/agents` instead.

## 5. Next Step

Continue with [Customize an Agent Core](customize-agent-core.md). It makes a
small file-backed change and verifies that the core still loads.
