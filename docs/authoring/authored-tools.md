# Authored Tools

Authored tools live under `agent/tools/` and are exposed through the host tool
runtime. They are core-local: installing or editing a tool changes one runtime
core, not the global host.

## Minimal Tool

```text
agent/tools/project_note/
  slot.yaml
  module.py
```

`agent/tools/project_note/slot.yaml`:

```yaml
entrypoint: module:run
description: "Return a short note about the current project."
input_schema:
  type: object
  properties:
    topic:
      type: string
  required:
    - topic
capabilities: []
risk: low
approval_policy: auto
```

`agent/tools/project_note/module.py`:

```python
from demiurge.sdk import ToolResult


def run(ctx, topic: str):
    ctx.output.notice(f"Checking note for {topic}")
    return ToolResult(content=f"No stored note for {topic}.")
```

## Expose the Tool

Core loader discovers `agent/tools/<tool_id>/slot.yaml` automatically. The host
combines authored tools with enabled built-in toolsets and MCP tools.

Tool names are the slot directory names.

## Delivery from Tools

Authored tools can call `ctx.output.send_text(...)`, media/file `send_*`,
`progress(...)`, and `notice(...)`. These deliveries use the tool slot's
default `history_policy` if the call omits one.

The returned `ToolResult` is still written separately as a model-visible tool
result.

## Shared Helpers

Put shared code under `agent/lib/` and import it through the slot package path.
Avoid bare imports that can collide with helpers from other slots.

## Success Check

Run:

```bash
uv run demiurge --provider fake
```

Then use `/tools`. The authored tool should appear if its slot loads and policy
allows it.

## Boundary

Authored tools are executed by the host. They do not bypass workspace checks,
capabilities, approvals, or event logging.
