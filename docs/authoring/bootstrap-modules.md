# Bootstrap Modules

Bootstrap modules build session-stable system context. The host runs them once
before the first input pipeline in a session and writes the result to
`bootstrap_context.md`.

## When to Use

Use bootstrap modules for context that should stay fixed for a session:

- project overview at session start;
- workspace inventory;
- stable operating constraints;
- expensive context snapshots.

Use input modules for context that must be recalculated every turn.

## Minimal Module

Create:

```text
agent/bootstrap/pipeline.yaml
agent/bootstrap/session_context/slot.yaml
agent/bootstrap/session_context/module.py
```

`agent/bootstrap/pipeline.yaml`:

```yaml
serial:
  - session_context
```

Bootstrap pipelines are serial-only.

Bootstrap slots can be hand-authored or installed by package recipes with
`kind: bootstrap`.

`agent/bootstrap/session_context/slot.yaml`:

```yaml
entrypoint: module:process
description: "Adds session-stable context."
failure_policy: soft
capabilities: []
```

`agent/bootstrap/session_context/module.py`:

```python
def process(ctx):
    ctx.bootstrap.add(f"Workspace: {ctx.bootstrap.workspace}")
```

## Runtime Behavior

The host joins successful additions with blank lines and writes exactly that
content to:

```text
~/.demiurge/sessions/<session_id>/bootstrap_context.md
```

An empty generated context still creates an empty file so bootstrap does not run
again on resume.

## Failure Policy

- `soft`: emit a failure event and continue without the module's additions.
- `hard`: block the first model request and do not write the bootstrap snapshot.

## Success Check

```bash
uv run demiurge --provider fake
ls ~/.demiurge/sessions/<session_id>/bootstrap_context.md
```

## Boundary

`ctx.bootstrap.add(text)` only appends system context. It does not write
`messages.jsonl`, create a user message, deliver channel output, or call the
provider.
