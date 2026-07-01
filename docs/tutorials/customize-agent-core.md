---
title: Customize an Agent Core
description: Make a small runtime Agent Core change, load it, and verify the authored surface.
---

# Customize an Agent Core

This tutorial adds a small input module to the runtime `assistant` core. The
module adds a current-turn style hint before the user's message reaches the
model.

You will edit only the runtime core under `~/.demiurge/agents/assistant`.

## 1. Start from a Working Core

Initialize the runtime home if needed:

```bash
uv run demiurge init
```

Check the core loads:

```bash
uv run demiurge init --check
```

## 2. Create the Input Slot

Create this directory:

```text
~/.demiurge/agents/assistant/agent/input/concise_hint/
```

Add `slot.yaml`:

```yaml
entrypoint: module:process
description: "Adds a concise-answer hint to the current turn."
failure_policy: soft
capabilities: []
```

Add `module.py`:

```python
def process(ctx):
    ctx.input.add("system", "For this turn, prefer a concise answer.")
```

The slot is core-local. It does not call the provider, execute tools, write
state, or bypass approvals.

## 3. Add the Slot to the Pipeline

Edit:

```text
~/.demiurge/agents/assistant/agent/input/pipeline.yaml
```

Place the hint before `base_input`:

```yaml
serial:
  - concise_hint
  - base_input
parallel: []
```

The input pipeline is ordered. `base_input` appends the raw user text, so hints
that should frame the current turn usually belong before it.

## 4. Verify the Core

```bash
uv run demiurge init --check
uv run demiurge --provider fake
```

Inside the TUI:

```text
/status
/exit
```

If the core does not load, inspect the exact error and compare the slot with
[../reference/contracts/slot-modules.md](../reference/contracts/slot-modules.md).

## 5. Undo the Change

Remove `concise_hint` from `pipeline.yaml`, then delete:

```text
~/.demiurge/agents/assistant/agent/input/concise_hint/
```

Run the same checks again:

```bash
uv run demiurge init --check
uv run demiurge --provider fake
```

## What You Learned

- Runtime cores are the live editable surface.
- Agent Slots are governed interaction boundaries loaded by the host.
- Pipelines decide when input and output modules run.
- Host-owned checks still control provider calls, tools, approvals, state, and
  promotion.
