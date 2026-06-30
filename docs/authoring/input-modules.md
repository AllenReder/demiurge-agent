# Input Modules

Input modules run before the model request. The host does not automatically add
channel input to the prompt; an input module must explicitly add user or system
content.

## Minimal Module

Create:

```text
agent/input/mobile_hint/
  slot.yaml
  module.py
```

Add it to `agent/input/pipeline.yaml`:

```yaml
serial:
  - base_input
  - mobile_hint
parallel: []
```

`agent/input/mobile_hint/slot.yaml`:

```yaml
entrypoint: module:process
description: "Adds a short-answer hint for Telegram conversations."
failure_policy: soft
history_policy: transient
capabilities: []
```

`agent/input/mobile_hint/module.py`:

```python
def process(ctx):
    if ctx.input.raw_input.metadata.get("channel") != "telegram":
        return
    ctx.input.add(
        "system",
        "The user is on Telegram. Prefer concise, scannable replies.",
    )
```

## Raw Input Passthrough

A normal passthrough module looks like this:

```python
def process(ctx):
    ctx.input.add("user", ctx.input.raw_input.text)
```

`system` fragments affect only the current provider request. `user` fragments
are joined into the current user message in serial order.

## Transient Input

Use transient input when content should affect the current request but not be
written to session history:

```python
ctx.input.add("user", "Temporary routing hint", history_policy="transient")
```

## Pre-Model Delivery

Input modules may emit user-visible status before the model call:

```python
def process(ctx):
    ctx.input.progress("Preparing context")
    ctx.input.send_text("Context prepared", history_policy="transient")
```

The host records events, applies history policy, and routes delivery.

## Schedule Input

When a schedule triggers a turn, `ctx.input.raw_input.text` is the schedule
`prompt`. Metadata includes `trigger`, `schedule_id`, `run_id`, `due_at`, and
`scheduled_at`.

## Success Check

```bash
uv run demiurge init --check
uv run demiurge --provider fake
```

Ask one prompt and inspect:

```bash
tail -n 20 ~/.demiurge/sessions/<session_id>/events.jsonl
tail -n 20 ~/.demiurge/sessions/<session_id>/messages.jsonl
```

## Boundary

Input modules should use `ctx.input`, `ctx.state`, `ctx.tools`, and other
host-injected clients. Do not write session files or call provider APIs
directly.
