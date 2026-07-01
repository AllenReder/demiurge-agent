---
title: 编写 Authored Tool
description: 添加一个 host 可以暴露给模型的 core-local tool。
---

# 编写 Authored Tool

Authored tools 位于 Agent Core 中，并由 host tool runtime 执行。

## 添加 Tool Directory

```text
agent/tools/project_note/
  slot.yaml
  module.py
```

## 定义 `slot.yaml`

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

Tool id 是目录名。Host 暴露 tool 时，模型会看到 description 和 input schema。

Authored tools 复用 `slot.yaml` metadata format，但它们不是 Agent Slots。Tool 是
model-callable action；slot 是 agent loop 中受治理的交互边界。

## 实现 `module.py`

```python
from demiurge.sdk import ToolResult


def execute(ctx, args):
    topic = args.get("topic") or "project"
    return ToolResult(content=f"Note about {topic}: keep changes scoped.")
```

## 需要时启用 Capabilities

如果 tool 需要 filesystem、terminal、network、state 或其他高风险效果，在
`slot.yaml` 中声明 capability，并在 core manifest 中配置 approval policy。

```yaml
capabilities:
  - fs.read
```

## 验证

```bash
uv run demiurge init --check
uv run demiurge --provider fake
```

在 TUI 中：

```text
/tools
```

## 边界

Authored tools 是 core-owned adapters。Host 仍然拥有 tool registration、capability
checks、approval checks、workspace scope、execution 和 result conversion。
