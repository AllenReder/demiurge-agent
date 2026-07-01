---
title: Choose a Workspace
description: Control the filesystem and terminal scope used by tools.
---

# Choose a Workspace

The workspace is the root used by file and terminal tools. It is part of the
host capability boundary.

## Local TUI Default

When you run the TUI from a project directory, that directory is the practical
workspace for local file and terminal work:

```bash
cd /path/to/project
uv run demiurge --provider fake
```

## Per-Run Override

```bash
uv run demiurge --workspace /path/to/project --provider fake
```

or:

```bash
DEMIURGE_WORKSPACE=/path/to/project uv run demiurge --provider fake
```

## Core Default

For gateway, Telegram, scheduler, and other non-local entry points, set the
runtime core default in `agent.yaml`:

```yaml
runtime:
  workspace: /path/to/project
```

If no override is available, Demiurge falls back to:

```text
~/.demiurge/workspace
```

## Verify

Inside the TUI:

```text
/status
```

The status view reports the resolved workspace and the source that selected it.

## Boundary

Workspace scope does not grant unlimited filesystem access. Sensitive paths and
dangerous operations still go through host-owned capabilities and approvals.
