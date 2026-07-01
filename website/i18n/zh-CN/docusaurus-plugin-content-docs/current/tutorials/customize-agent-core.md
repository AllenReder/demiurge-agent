---
title: 修改 Agent Core
description: 修改一个小的 runtime Agent Core 文件，加载它，并验证 authored surface。
---

# 修改 Agent Core

本教程会给 runtime `assistant` core 添加一个小 input module。这个 module 会在
用户消息进入模型之前，为当前 turn 加上一条风格提示。

你只会编辑 `~/.demiurge/agents/assistant` 下的 runtime core。

## 1. 从可工作的 Core 开始

如果还没有初始化 runtime home：

```bash
uv run demiurge init
```

检查 core 能加载：

```bash
uv run demiurge init --check
```

## 2. 创建 Input Slot

创建这个目录：

```text
~/.demiurge/agents/assistant/agent/input/concise_hint/
```

添加 `slot.yaml`：

```yaml
entrypoint: module:process
description: "Adds a concise-answer hint to the current turn."
failure_policy: soft
capabilities: []
```

添加 `module.py`：

```python
def process(ctx):
    ctx.input.add("system", "For this turn, prefer a concise answer.")
```

这个 slot 是 core-local 的。它不会调用 provider、执行 tools、写 state，或绕过
approval。

## 3. 把 Slot 加入 Pipeline

编辑：

```text
~/.demiurge/agents/assistant/agent/input/pipeline.yaml
```

把提示放在 `base_input` 之前：

```yaml
serial:
  - concise_hint
  - base_input
parallel: []
```

Input pipeline 是有顺序的。`base_input` 会追加原始用户文本，所以用于框定当前
turn 的提示通常应该放在它之前。

## 4. 验证 Core

```bash
uv run demiurge init --check
uv run demiurge --provider fake
```

在 TUI 中运行：

```text
/status
/exit
```

如果 core 无法加载，检查精确错误，并对照
[../reference/contracts/slot-modules.md](../reference/contracts/slot-modules.md)。

## 5. 撤销修改

从 `pipeline.yaml` 中移除 `concise_hint`，然后删除：

```text
~/.demiurge/agents/assistant/agent/input/concise_hint/
```

再次运行同样的检查：

```bash
uv run demiurge init --check
uv run demiurge --provider fake
```

## 你学到了什么

- Runtime core 是 live editable surface。
- Slot module 是由 host 加载的普通文件。
- Pipeline 决定 input 和 output module 何时运行。
- Provider calls、tools、approvals、state 和 promotion 仍由 host-owned checks
  控制。
