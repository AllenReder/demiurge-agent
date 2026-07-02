---
title: 定制 Agent Core
description: 向一个具体运行时 Agent Core 添加小型 input slot 并验证它。
---

# 定制 Agent Core

本教程会向运行时 `assistant` core 添加一个 input slot。这个 slot 会在模型看到用户消息之前，添加一条当前 turn 的指令。

你将编辑这个具体 core：

```text
~/.demiurge/agents/assistant/
```

本教程不要编辑 `~/.demiurge/agents/agent.yaml`。该文件是全局 fallback 配置，不是 Agent Core。

## 开始之前

如果需要，初始化 runtime home：

```bash
uv run demiurge init
```

检查当前运行时 cores 是否可以加载：

```bash
uv run demiurge init --check
```

一个具体 core 必须同时包含这两个文件：

```text
~/.demiurge/agents/assistant/agent.yaml
~/.demiurge/agents/assistant/agent/pipelines.yaml
```

`agent.yaml` 会通过 `runtime.surface_root` 指向 loader 使用的 authored surface，通常是 `agent`。Bootstrap、input 和 output slot 目录都会从这个 surface root 解析。

## 创建 Slot

创建这个目录：

```text
~/.demiurge/agents/assistant/agent/input/concise_hint/
```

添加 `module.py`：

```python
def process(ctx):
    ctx.input.add_context(
        "For this turn, prefer a concise answer with concrete next steps.",
        role="system",
    )
```

添加 `slot.yaml`：

```yaml
entrypoint: module:process
description: "Adds a concise-answer hint to the current turn."
capabilities: []
failure_policy: soft
```

这个 slot 不会调用 tools、写入 state、触碰文件或绕过 approvals。

## 将 Slot 添加到现有 Pipeline

打开现有文件：

```text
~/.demiurge/agents/assistant/agent/pipelines.yaml
```

保留现有文件，并在 `base_input` 之前把新的 slot id 插入 `input.serial`：

```yaml
input:
  serial:
    - concise_hint
    - base_input
```

不要替换整个 `pipelines.yaml` 文件。除非你有意修改，否则保留现有的 `schema_version`、`bootstrap`、`output` 和 `parallel` 条目。

`base_input` 是 seed input slot，会追加原始用户文本。需要框定用户消息的提示通常应在它之前运行。

## 验证 Core

再次运行 loader 检查：

```bash
uv run demiurge init --check
```

然后启动一次 fake-provider turn：

```bash
uv run demiurge --provider fake
```

在 TUI 中检查运行时状态并退出：

```text
/status
/exit
```

如果 core 加载失败，请对照 [slot module contract](../reference/contracts/slot-modules.md) 检查 slot 目录。

## 撤销更改

只从 `input.serial` 中移除 `concise_hint`，保留 `agent/pipelines.yaml` 其余内容不变。然后删除：

```text
~/.demiurge/agents/assistant/agent/input/concise_hint/
```

运行同样的 loader 检查：

```bash
uv run demiurge init --check
```

## 你学到了什么

- `agents/agent.yaml` 是全局 fallback 层。
- 具体 cores 位于 `agents/<core>/agent.yaml` 加上 `agents/<core>/agent/`。
- Slot 目录从 `runtime.surface_root` 加载。
- `agent/pipelines.yaml` 控制 bootstrap、input 和 output 阶段顺序。
- Host 仍然拥有 provider calls、tool dispatch、approvals、state、version promotion 和 rollback。
