---
title: Write an Authored Tool
description: Add a core-local tool that the host can expose to the model.
---

# Write an Authored Tool

Authored tools live in the Agent Core and are executed by the host tool runtime.

## Add the Tool Directory

```text
agent/tools/project_note/
  tool.yaml
  module.py
```

## Define `tool.yaml`

```yaml
entrypoint: module:execute
description: "Return a short project note."
input_schema:
  type: object
  properties:
    topic:
      type: string
  additionalProperties: false
capabilities: []
```

The tool id is the directory name. The model sees the description and input
schema when the host exposes the tool.

Authored tools are not Agent Slots and are not declared in `agent/slots.yaml`.
A tool is a model-callable action; a slot is a governed interaction boundary in
the agent loop.

## Implement `module.py`

```python
from demiurge.sdk import ToolResult


def execute(ctx, args):
    topic = args.get("topic") or "project"
    return ToolResult(content=f"Note about {topic}: keep changes scoped.")
```

## Enable Capabilities When Needed

If a tool needs filesystem, terminal, network, state, or other dangerous
effects, declare the capability in `tool.yaml` and configure approval policy in
the core manifest.

```yaml
capabilities:
  - fs.read
```

## Verify

```bash
uv run demiurge init --check
uv run demiurge --provider fake
```

Inside the TUI:

```text
/tools
```

## Boundary

Authored tools are core-owned adapters. The host still owns tool registration,
capability checks, approval checks, workspace scope, execution, and result
conversion.
