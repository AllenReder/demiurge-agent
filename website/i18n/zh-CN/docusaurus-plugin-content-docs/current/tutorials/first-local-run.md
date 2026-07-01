---
title: 快速开始
description: 用 fake provider 在本地启动 Demiurge，然后选择下一步配置。
---

# 快速开始

这是打开 Demiurge TUI 的最短路径。它使用 fake provider，所以暂时不需要 API key。

TUI 能打开以后，再根据文末链接配置真实 provider 或安装 packages。

## 1. 选择运行方式

Managed user install：

```bash
scripts/install.sh
```

Installer 会打印 managed command path。默认是：

```bash
~/.demiurge/demiurge-agent/.venv/bin/demiurge
```

Source checkout 开发：

```bash
uv sync --all-groups
```

后续命令使用 `uv run demiurge`。

## 2. 初始化一次

Managed install：

```bash
~/.demiurge/demiurge-agent/.venv/bin/demiurge init
```

Source checkout：

```bash
uv run demiurge init
```

## 3. 启动 TUI

Managed install：

```bash
~/.demiurge/demiurge-agent/.venv/bin/demiurge --provider fake
```

Source checkout：

```bash
uv run demiurge --provider fake
```

TUI 应该能打开，并且不要求任何 provider secrets。

## 4. 确认可用

在 TUI 中运行：

```text
/status
/exit
```

`/status` 应显示当前 core、runtime home、workspace、provider 和 session path。

## 5. 下一步

选择下一项任务：

- 用 [配置 Provider](../how-to/configure-provider.md) 配置真实模型 provider。
- 用 [安装 Packages](../how-to/install-packages.md) 安装可复用能力。
- 用 [修改 Agent Core](customize-agent-core.md) 修改 runtime Agent Core。
- 用 [故障排查](../how-to/troubleshoot.md) 诊断启动问题。

## 常用检查

如果启动失败，运行：

```bash
uv run demiurge init --check
uv run demiurge doctor
```

Managed install 时，把 `uv run demiurge` 替换成：

```bash
~/.demiurge/demiurge-agent/.venv/bin/demiurge
```
