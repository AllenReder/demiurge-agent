---
title: 首次本地运行
description: 安装或同步 Demiurge，初始化 runtime home，并用 fake provider 启动 TUI。
---

# 首次本地运行

本教程会在不需要 API key 的情况下让 Demiurge 在本地跑起来。它会验证 host
runtime、runtime home、source templates、TUI bridge 和 session storage。

先使用 fake provider。只有这条路径跑通后，再配置真实模型。

## 1. 安装或同步

如果你要做 managed user install：

```bash
scripts/install.sh
```

Managed checkout 位于：

```text
~/.demiurge/demiurge-agent
```

如果你在 source checkout 中开发：

```bash
uv sync --all-groups
```

确认命令可用：

```bash
uv run demiurge --help
```

## 2. 初始化 Runtime Home

```bash
uv run demiurge init
```

这会创建或刷新本地 runtime 结构：

```text
~/.demiurge/
  config.yaml
  .env
  agents/
    agent.yaml
    assistant/
    evolver/
  workspace/
```

不写入文件，只检查 template drift：

```bash
uv run demiurge init --check
uv run demiurge doctor
```

## 3. 启动 TUI

```bash
uv run demiurge --provider fake
```

默认本地界面是 TUI。它通过 stdio JSON-RPC 连接到 Python host。Wheel 包含已构建
的 TUI asset，所以只有在编辑 `ui-tui/` 时才需要 Node.js。

在 TUI 中运行：

```text
/status
/tools
/sessions
/exit
```

`/status` 应显示当前 core、runtime home、workspace、provider、model source
和 session path。

## 4. 找到 Live Agent Core

Runtime Agent Core 位于：

```text
~/.demiurge/agents/<core_id>/
```

默认 assistant core 是：

```text
~/.demiurge/agents/assistant/
```

实验 live agent 时，不要编辑仓库里的 source templates。请编辑
`~/.demiurge/agents` 下的 runtime core。

## 5. 下一步

继续阅读 [修改 Agent Core](customize-agent-core.md)。它会做一次小的文件化修改，
并验证 core 仍能加载。
