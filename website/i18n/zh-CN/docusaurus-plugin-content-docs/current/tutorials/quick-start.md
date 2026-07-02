---
title: 快速开始
description: 使用 fake provider 在本地启动 Demiurge TUI，不需要 API key。
---

# 快速开始

本教程会用 fake provider 启动 Demiurge TUI。它不需要 API key，因此是最安全的
首次运行方式。

完成后，你会得到一个正在运行的 TUI、可见的 `/status` 报告，以及后续设置任务的链接。

## 开始之前

安装：

- `git`
- `uv`
- Node.js 20 或更新版本

managed install path 最适合正常使用。source checkout path 用于开发 Demiurge 本身。

## 1. 选择安装路径

对于 managed install，请从 Demiurge 仓库的 checkout 中运行：

```bash
scripts/install.sh
```

installer 要求 `git` 和 `uv`，会创建或复用位于
`~/.demiurge/demiurge-agent` 的 managed checkout，运行 `uv sync`，并初始化
runtime home。command path 是：

```bash
~/.demiurge/demiurge-agent/.venv/bin/demiurge
```

对于 source checkout 开发，请运行：

```bash
uv sync --all-groups
uv run demiurge init
```

然后在下面的命令中使用 `uv run demiurge`。

## 2. 启动 TUI

Managed install：

```bash
~/.demiurge/demiurge-agent/.venv/bin/demiurge --provider fake
```

Source checkout：

```bash
uv run demiurge --provider fake
```

不带 subcommand 运行 `demiurge` 会启动 TUI。`--provider fake` override 让首次运行
不依赖 provider setup。

## 3. 确认 Runtime

在 TUI 中运行：

```text
/status
/exit
```

`/status` 应显示选中的 core、runtime home、workspace、provider 和 session path。

如果 workspace 不是你预期的项目，请从那个目录重新启动，或传入
`--workspace /path/to/project`。

## 4. 了解命令入口

顶层 subcommands 是：

- `init`
- `doctor`
- `package`
- `update`
- `setup`
- `gateway`

不带其他 subcommand 运行 `demiurge setup` 会打开 setup wizard。

## 5. 下一步

选择下一项任务：

- 用 [配置 Provider](../how-to/configure-provider.md) 配置真实模型 provider。
- 用 [选择 Workspace](../how-to/choose-workspace.md) 选择合适的文件和 terminal scope。
- 用 [安装 Packages](../how-to/install-packages.md) 安装可复用能力。
- 用 [修改 Agent Core](customize-agent-core.md) 修改 runtime Agent Core。
- 用 [故障排查](../how-to/troubleshoot.md) 诊断启动问题。

## 如果启动失败

运行只读检查：

```bash
uv run demiurge init --check
uv run demiurge doctor
```

Managed install 时，把 `uv run demiurge` 替换成：

```bash
~/.demiurge/demiurge-agent/.venv/bin/demiurge
```
