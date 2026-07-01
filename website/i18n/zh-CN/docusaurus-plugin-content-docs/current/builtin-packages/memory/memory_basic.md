---
sidebar_position: 1
title: memory_basic
description: 安装并使用内置 file-backed memory package。
---

# memory_basic

`memory_basic` 为 Agent Core 添加本地持久记忆文件。它是最小的内置 memory
package：不需要外部服务、不访问网络，也不需要 locked Demiurge 环境之外的
Python dependency。

当你只需要简单的 Hermes-style `USER.md` 和 `MEMORY.md` recall 时使用它。

## 安装内容

Package 会安装：

```text
agent/lib/memory_basic/
agent/bootstrap/memory_basic/
agent/tools/memory/
```

它还会编辑 bootstrap pipeline：

```yaml
agent/bootstrap/pipeline.yaml:
  serial:
    - memory_basic
```

如果 core 已经有 `session_context` bootstrap slot，package installer 会把
`memory_basic` 插在它后面。

持久 memory 文件位于 package-owned component directories 之外：

```text
memory/
  MEMORY.md
  USER.md
```

Uninstall 会移除 package-owned lib、bootstrap 和 tool files，但保留
`memory/` data directory。

## 安装

先 preview：

```bash
uv run demiurge package install memory_basic --core assistant --preview
```

安装：

```bash
uv run demiurge package install memory_basic --core assistant
```

## Runtime 行为

Session bootstrap 时，`memory_basic` 会读取 `memory/MEMORY.md` 和
`memory/USER.md`，并把 frozen memory snapshot 注入 host bootstrap context。
这个 snapshot 会在整个 session 中复用。同一个 session 中写入的内容对
`memory` tool 可见，但直到新 session 开始前不会注入 model prompt。

默认字符预算：

| Store | 默认限制 |
| --- | --- |
| `MEMORY.md` | 2200 chars |
| `USER.md` | 1375 chars |

## Tool

Package 会安装一个 authored tool：

| Tool | Approval | 用途 |
| --- | --- | --- |
| `memory` | `auto` | 在 `MEMORY.md` 和 `USER.md` 中 add、replace、remove 或 list entries。 |

用 `target=memory` 记录项目约定、环境事实和工作流经验。用 `target=user` 记录稳定的用户资料或偏好。只有 `action=list` 时才使用 `target=all`。

Tool 支持单个 operation：

```json
{"target": "memory", "action": "add", "content": "Use uv for Python commands."}
```

也支持 batch operations：

```json
{
  "target": "memory",
  "operations": [
    {"action": "remove", "old_text": "old convention"},
    {"action": "add", "content": "new convention"}
  ]
}
```

Batch writes 会针对最终字符预算保持 all-or-nothing。

## 验证

列出已安装 packages：

```bash
uv run demiurge package list --core assistant
```

Memory write 之后检查：

```text
~/.demiurge/agents/assistant/memory/MEMORY.md
~/.demiurge/agents/assistant/memory/USER.md
```

## 卸载

```bash
uv run demiurge package uninstall memory_basic --core assistant --preview
uv run demiurge package uninstall memory_basic --core assistant
```

Uninstall 会恢复 bootstrap pipeline，并移除 package-owned component
directories。它不会移除 `memory/MEMORY.md` 或 `memory/USER.md`。

## 何时改用 memory_honcho

当你需要 Honcho-backed cross-session modeling、automatic remote recall、
completed-turn sync 或显式 `honcho_*` tools 时，使用
[`memory_honcho`](memory_honcho.md)。只需要本地 file-backed memory 时，使用
`memory_basic`。
