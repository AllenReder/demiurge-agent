# Output Modules

Output modules run after the model has finished. The host does not
automatically deliver assistant text; an output module must call `ctx.output`.

## Minimal Module

Create:

```text
agent/output/base_output/
  slot.yaml
  module.py
```

`agent/output/pipeline.yaml`:

```yaml
serial:
  - base_output
parallel: []
```

`agent/output/base_output/slot.yaml`:

```yaml
entrypoint: module:process
description: "Deliver final assistant text."
failure_policy: hard
history_policy: persist
capabilities: []
```

`agent/output/base_output/module.py`:

```python
def process(ctx):
    ctx.output.send_text(ctx.output.content, history_policy="persist")
```

If no output module calls `ctx.output.send_*`, the final model output is not
delivered and is not written as a normal assistant reply.

## Immediate and Slot-End Delivery

```python
ctx.output.progress("Generating artifact")
ctx.output.send_text(
    "Artifact ready",
    history_policy="model_hidden",
    delivery="immediate",
)
ctx.output.send_text(
    "Visible after this slot succeeds",
    history_policy="transient",
    delivery="slot_end",
)
```

`progress()` and `notice()` are always transient immediate deliveries.

## Structured Results

Output modules can return code-level structured data to a parent core:

```python
def process(ctx):
    ctx.result.set({
        "summary": ctx.output.content,
        "kind": "text",
    })
```

Only serial output modules can write `ctx.result`. Parallel output modules
cannot affect parent-visible structured data.

## Artifacts and Media

Use media/file delivery methods for registered artifacts:

```python
ctx.output.send_file(
    "report.md",
    summary="Generated report",
    history_policy="model_hidden",
)
```

The first argument must be a workspace/session path, URL, or host-returned
`ArtifactRef`.

## Success Check

```bash
uv run demiurge --provider fake
```

Then inspect `messages.jsonl` and `events.jsonl`. A normal final response should
appear as a persisted assistant message.

## Boundary

Output modules are delivery and result shapers. They do not own channel SDKs,
provider calls, or session persistence; the host handles those from the
delivery request.
