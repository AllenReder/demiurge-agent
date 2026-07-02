---
title: 选择工作区
description: 控制 tools 使用的 filesystem 和 terminal 作用域。
---

# 选择工作区

Workspace 是 file 和 terminal tools 使用的根目录。它是 host capability boundary 的一部分：
选择 workspace 会给 tools 一个项目根目录，但 approvals 和 sensitive-path checks
仍然生效。

对于 managed install，请把 `uv run demiurge` 替换成：

```bash
~/.demiurge/demiurge-agent/.venv/bin/demiurge
```

## 解析顺序

Demiurge 按以下顺序解析 workspace：

1. CLI `--workspace`。
2. `DEMIURGE_WORKSPACE`。
3. TUI launch current working directory。
4. 选中 core 的 `runtime.workspace`。
5. `~/.demiurge/workspace`。

请选择优先级最高且符合可重复性需求的选项。

## 使用 launch directory 做本地 TUI 工作

当你在项目目录中运行 TUI 时，该目录就是本地 file 和 terminal 工作的实际 workspace：

```bash
cd /path/to/project
uv run demiurge --provider fake
```

这个 fallback 适用于 TUI，因为 launcher 会把自己的 current working directory 传给
runtime。

## 单次运行覆盖

```bash
uv run demiurge --workspace /path/to/project --provider fake
```

将它用于一次性 session，或用于没有从项目目录启动 TUI 的情况。

## 使用环境变量

```bash
DEMIURGE_WORKSPACE=/path/to/project uv run demiurge --provider fake
```

当某个 shell、script 或 terminal profile 应始终指向同一个 workspace 时使用它。

## 设置 Core 默认值

对于 gateway、scheduler、Telegram 和其他非本地入口点，请在选中 runtime core 的
`agent.yaml` 中设置 runtime core default：

```yaml
runtime:
  workspace: /path/to/project
```

Relative paths 会从 runtime core root 解析。对于长时间运行的 channels，absolute
paths 更不容易出意外。

## 默认 Workspace

如果没有可用的 override，Demiurge 会创建并使用：

```text
~/.demiurge/workspace
```

## 验证

在 TUI 内：

```text
/status
```

status 视图会显示解析后的 workspace，以及选择它的来源。

## 常见错误

- 从 `~/.demiurge/demiurge-agent` 启动 TUI 会把 managed checkout 作为
  launch-directory fallback。请从你的项目中运行，或传入 `--workspace`。
- 设置 `runtime.workspace` 不会覆盖 `--workspace` 或 `DEMIURGE_WORKSPACE`。
- Workspace 不会绕过 approvals。破坏性或 sensitive operations 仍然经过
  host-owned capabilities。
