---
title: Customize an Agent Core
description: Add a small input slot to a concrete runtime Agent Core and verify it.
---

# Customize an Agent Core

This tutorial adds one input slot to the runtime `assistant` core. The slot adds
a current-turn instruction before the model sees the user's message.

You will edit the concrete core under:

```text
~/.demiurge/agents/assistant/
```

Do not edit `~/.demiurge/agents/agent.yaml` for this tutorial. That file is the
global fallback config, not an Agent Core.

## Before You Start

Initialize the runtime home if needed:

```bash
uv run demiurge init
```

Check that the current runtime cores load:

```bash
uv run demiurge init --check
```

A concrete core must contain both of these files:

```text
~/.demiurge/agents/assistant/agent.yaml
~/.demiurge/agents/assistant/agent/pipelines.yaml
```

`agent.yaml` points the loader at `runtime.surface_root`, which is normally
`agent`. Bootstrap, input, and output slot directories are resolved from that
surface root.

## Create the Slot

Create this directory:

```text
~/.demiurge/agents/assistant/agent/input/concise_hint/
```

Add `module.py`:

```python
def process(ctx):
    ctx.input.add_context(
        "For this turn, prefer a concise answer with concrete next steps.",
        role="system",
    )
```

Add `slot.yaml`:

```yaml
entrypoint: module:process
description: "Adds a concise-answer hint to the current turn."
capabilities: []
failure_policy: soft
```

This slot does not call tools, write state, touch files, or bypass approvals.

## Add the Slot to the Existing Pipeline

Open the existing file:

```text
~/.demiurge/agents/assistant/agent/pipelines.yaml
```

Keep the existing file and insert the new slot id into `input.serial` before
`base_input`:

```yaml
input:
  serial:
    - concise_hint
    - base_input
```

Do not replace the whole `pipelines.yaml` file. Leave the existing
`schema_version`, `bootstrap`, `output`, and `parallel` entries in place unless
you are intentionally changing them.

`base_input` is the seed input slot that appends the raw user text. Hints that
should frame the user's message usually run before it.

## Verify the Core

Run the loader check again:

```bash
uv run demiurge init --check
```

Then start a fake-provider turn:

```bash
uv run demiurge --provider fake
```

Inside the TUI, check the runtime status and exit:

```text
/status
/exit
```

If the core fails to load, compare the slot directory with
[the slot module contract](../reference/contracts/slot-modules.md).

## Undo the Change

Remove only `concise_hint` from `input.serial`, leaving the rest of
`agent/pipelines.yaml` intact. Then delete:

```text
~/.demiurge/agents/assistant/agent/input/concise_hint/
```

Run the same loader check:

```bash
uv run demiurge init --check
```

## What You Learned

- `agents/agent.yaml` is the global fallback layer.
- Concrete cores live under `agents/<core>/agent.yaml` plus `agents/<core>/agent/`.
- Slot directories are loaded from `runtime.surface_root`.
- `agent/pipelines.yaml` controls the bootstrap, input, and output phase order.
- The host still owns provider calls, tool dispatch, approvals, state, Git
  revision promotion, and rollback.
