---
title: 选择工作区
description: 控制 tools 使用的 filesystem 和 terminal 范围。
---

# 选择工作区

Workspace 是 file 和 terminal tools 使用的根目录。它属于 host capability
boundary 的一部分。

## 本地 TUI 默认值

当你在项目目录中运行 TUI 时，该目录就是本地 file 和 terminal 工作的实际 workspace：

```bash
cd /path/to/project
uv run demiurge --provider fake
```

## 单次运行覆盖

```bash
uv run demiurge --workspace /path/to/project --provider fake
```

或者：

```bash
DEMIURGE_WORKSPACE=/path/to/project uv run demiurge --provider fake
```

## Core 默认值

对于 gateway、Telegram、scheduler 和其他非本地入口点，在 `agent.yaml` 中设置 runtime
core default：

```yaml
runtime:
  workspace: /path/to/project
```

如果没有可用的 override，Demiurge 会回退到：

```text
~/.demiurge/workspace
```

## 验证

在 TUI 内：

```text
/status
```

status 视图会显示解析后的 workspace，以及选择它的来源。

## 边界

Workspace 范围不会赋予无限 filesystem access。敏感路径和危险操作仍然要经过
host-owned capabilities 和 approvals。
