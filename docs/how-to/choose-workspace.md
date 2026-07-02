---
title: Choose a Workspace
description: Control the filesystem and terminal scope used by tools.
---

# Choose a Workspace

The workspace is the root used by file and terminal tools. It is part of the
host capability boundary: choosing a workspace gives tools a project root, but
approvals and sensitive-path checks still apply.

For a managed install, replace `uv run demiurge` with:

```bash
~/.demiurge/demiurge-agent/.venv/bin/demiurge
```

## Resolution Order

Demiurge resolves the workspace in this order:

1. CLI `--workspace`.
2. `DEMIURGE_WORKSPACE`.
3. TUI launch current working directory.
4. The selected core's `runtime.workspace`.
5. `~/.demiurge/workspace`.

Use the highest-priority option that matches how repeatable the setting should
be.

## Use the Launch Directory for Local TUI Work

When you run the TUI from a project directory, that directory is the practical
workspace for local file and terminal work:

```bash
cd /path/to/project
uv run demiurge --provider fake
```

This fallback applies to the TUI because the launcher passes its current working
directory to the runtime.

## Override One Run

```bash
uv run demiurge --workspace /path/to/project --provider fake
```

Use this for a one-off session or when you are not starting the TUI from the
project directory.

## Use an Environment Variable

```bash
DEMIURGE_WORKSPACE=/path/to/project uv run demiurge --provider fake
```

Use this when a shell, script, or terminal profile should consistently target
one workspace.

## Set a Core Default

For gateway, scheduler, Telegram, and other non-local entry points, set the
runtime core default in the selected runtime core's `agent.yaml`:

```yaml
runtime:
  workspace: /path/to/project
```

Relative paths are resolved from the runtime core root. Absolute paths are less
surprising for long-running channels.

## Default Workspace

If no override is available, Demiurge creates and uses:

```text
~/.demiurge/workspace
```

## Verify

Inside the TUI:

```text
/status
```

The status view reports the resolved workspace and the source that selected it.

## Common Mistakes

- Starting the TUI from `~/.demiurge/demiurge-agent` makes the managed checkout
  the launch-directory fallback. Run from your project or pass `--workspace`.
- Setting `runtime.workspace` does not override `--workspace` or
  `DEMIURGE_WORKSPACE`.
- A workspace does not bypass approvals. Destructive or sensitive operations
  still go through host-owned capabilities.
