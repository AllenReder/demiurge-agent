---
title: 编写 Authored Tool
description: 在 authored surface 下添加 core-local、可由模型调用的 tool。
---

# 编写 Authored Tool

Authored tools 是公开的 Agent Core 文件，host tool runtime 可以把它们暴露给模型。它们位于具体 core 的 `agent.yaml` 中配置的 tool root 下。

源 `assistant` core 使用：

```yaml
slots:
  tools: agent/tools
```

使用这个设置时，名为 `project_note` 的 tool 位于：

```text
agent/tools/project_note/
  tool.yaml
  module.py
```

Authored tools 不是 input 或 output slots。不要把它们添加到 `agent/pipelines.yaml`。

## 定义 `tool.yaml`

创建 `agent/tools/project_note/tool.yaml`：

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

Tool id 是目录名。Host 暴露 tool 时，模型会看到 description 和 `input_schema`。

请选择不会与已选择 builtin tool 或已发现 MCP tool 冲突的 id。Builtin/authored collision
会让 core loading 失败，MCP collision 会让最终 catalog 构建失败。错误会列出两侧
provenance；应重命名 authored tool，而不是依赖 source 优先级。

单数的 `capability` 是这个 tool 的 metadata 和 approval policy 使用的主要 registry capability。`capabilities` 列表不同：它声明 tool implementation 可能通过 `ctx.capability.require(...)` 需要的 capabilities。

## 实现 `module.py`

创建 `agent/tools/project_note/module.py`：

```python
from demiurge.sdk import ToolResult


def execute(ctx, args):
    topic = args.get("topic") or "project"
    return ToolResult(content=f"Note about {topic}: keep changes scoped.")
```

默认 authored tool entrypoint 是 `module:execute`。

## 在需要时声明 Effect Capabilities

如果 tool implementation 执行 host-guarded effect，请把该 effect 添加到 `capabilities`，并在代码中 require 它。

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

声明 `capability` 不会授予 filesystem、terminal、network、state 或 agent 权限。只授予 implementation 实际使用的具体 effect capabilities。

## 从 `agent.yaml` 覆盖 Metadata

当你需要隐藏某个 tool，或不编辑 tool 目录就覆盖 registry metadata 时，使用 `tools.metadata`：

```yaml
tools:
  metadata:
    project_note:
      approval_policy: prompt
      risk: medium
```

对于 authored tools，metadata overrides 可以降低或提高 risk 和 approval policy。Built-in tools 更受限制：core metadata 不能降低它们的 risk，也不能弱化 approval policy。

## 验证

运行：

```bash
uv run demiurge init --check
uv run demiurge --provider fake
```

在 TUI 中：

```text
/tools
```

这个 tool 应该显示为 authored tool。如果没有，请确认：

- `agent.yaml` 有 `slots.tools: agent/tools`。
- Tool 目录包含 `tool.yaml`。
- `tool.yaml` 只使用支持的字段。
- Tool id 与目录名匹配。
- Tool id 不与 builtin 或 MCP 的 model-visible name 冲突。
