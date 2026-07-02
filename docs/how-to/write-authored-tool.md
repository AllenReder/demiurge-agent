---
title: Write an Authored Tool
description: Add a core-local model-callable tool under the authored surface.
---

# Write an Authored Tool

Authored tools are public Agent Core files that the host tool runtime can expose
to the model. They live under the tool root configured in the concrete core's
`agent.yaml`.

The source `assistant` core uses:

```yaml
slots:
  tools: agent/tools
```

With that setting, a tool named `project_note` lives at:

```text
agent/tools/project_note/
  tool.yaml
  module.py
```

Authored tools are not input or output slots. Do not add them to
`agent/pipelines.yaml`.

## Define `tool.yaml`

Create `agent/tools/project_note/tool.yaml`:

```yaml
entrypoint: module:execute
description: "Return a short project note."
input_schema:
  type: object
  properties:
    topic:
      type: string
  additionalProperties: false
risk: low
capability: tool.call:project_note
approval_policy: auto
display_policy: summary
model_output_policy: content
capabilities: []
```

The tool id is the directory name. The model sees the description and
`input_schema` when the host exposes the tool.

The singular `capability` is the primary registry capability for this tool's
metadata and approval policy. The `capabilities` list is different: it declares
capabilities that the tool implementation may require through
`ctx.capability.require(...)`.

## Implement `module.py`

Create `agent/tools/project_note/module.py`:

```python
from demiurge.sdk import ToolResult


def execute(ctx, args):
    topic = args.get("topic") or "project"
    return ToolResult(content=f"Note about {topic}: keep changes scoped.")
```

The default authored tool entrypoint is `module:execute`.

## Declare Effect Capabilities When Needed

If the tool implementation performs a host-guarded effect, add that effect to
`capabilities` and require it in code.

```yaml
capability: tool.call:workspace_note
capabilities:
  - fs.read
```

```python
from pathlib import Path

from demiurge.sdk import ToolResult


def execute(ctx, args):
    ctx.capability.require("fs.read", slot_path=ctx.slot_path)
    path = Path(ctx.workspace) / "README.md"
    return ToolResult(content=path.read_text(encoding="utf-8")[:500])
```

Declaring `capability` does not grant filesystem, terminal, network, state, or
agent authority. Grant only the specific effect capabilities the implementation
uses.

## Override Metadata from `agent.yaml`

Use `tools.metadata` when you need to hide a tool or override registry metadata
without editing the tool directory:

```yaml
tools:
  metadata:
    project_note:
      approval_policy: prompt
      risk: medium
```

For authored tools, metadata overrides can lower or raise risk and approval
policy. Built-in tools are more restrictive: core metadata cannot lower their
risk or weaken their approval policy.

## Verify

Run:

```bash
uv run demiurge init --check
uv run demiurge --provider fake
```

Inside the TUI:

```text
/tools
```

The tool should appear as an authored tool. If it does not, confirm that:

- `agent.yaml` has `slots.tools: agent/tools`.
- The tool directory contains `tool.yaml`.
- `tool.yaml` uses only supported fields.
- The tool id matches the directory name.
