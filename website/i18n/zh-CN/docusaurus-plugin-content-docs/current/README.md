---
slug: /
sidebar_position: 0
title: Demiurge 手册
description: 用于安装 Demiurge、配置 provider、选择 workspace，以及用 Agent Slots 编写可自进化 Agent Core 的用户手册。
---

# Demiurge 手册

Demiurge 是 Alpha 阶段的智能体框架，围绕 **Agent Slots** 构建：Agent Slots
是受治理的扩展边界，让 Agent Core 可以拓展能力边界与逻辑设计，而不需要修改
Host harness。具有文件化设计的 Agent Core 可以组合 agents、state、tools、
skills 和 MCP declarations，并通过 Host 控制的 Git change sets 实现自我演进。

Host 拥有 session、turn、provider call、tool、approval、state、delivery、
Git revision promotion 和 rollback。Agent Core 拥有作者维护的文件，例如
`agent.yaml`、`SOUL.md`、Agent Slots、skills、tools、schedules、MCP
declarations 和本地 libraries。

如果你想理解自定义行为如何在 Host 治理下进入 agent loop，请先读
[Agent Slots](explanation/agent-slots.md)。

本手册采用 Diataxis 文档模型：

- **教程**引导你完成一条完整的学习路径。
- **操作指南**解决一个具体的运维任务。
- **解释**页面说明系统为什么这样设计。
- **参考**页面定义精确的命令、schema 和 contract。

参考里的 contract 页面也设计为可被 `evolver` core 阅读，前提是它们作为只读
项目文档上下文提供给该 core。

## 从这里开始

如果你刚开始使用 Demiurge，请按顺序阅读：

1. [快速开始](tutorials/quick-start.md)
2. [配置 provider](how-to/configure-provider.md)
3. [选择 workspace](how-to/choose-workspace.md)
4. [故障排查](how-to/troubleshoot.md)

## 按角色阅读

| 角色 | 首先阅读 |
| --- | --- |
| 首次用户 | [快速开始](tutorials/quick-start.md), [配置 provider](how-to/configure-provider.md), [选择 workspace](how-to/choose-workspace.md) |
| 本地运行者 | [故障排查](how-to/troubleshoot.md), [配置 channels](how-to/configure-channels.md), [安装 packages](how-to/install-packages.md) |
| Agent Core 作者 | [Host 和 Agent Core](explanation/host-and-agent-core.md), [修改 Agent Core](tutorials/customize-agent-core.md), [编写 Agent Slot](how-to/write-slot-module.md), [Slot Context SDK](reference/slot-context-sdk.md), [Authored surface contract](reference/contracts/authored-surface.md) |
| Package 作者 | [Package 模型](explanation/package-model.md), [编写 Package Recipe](how-to/write-package-recipe.md), [创建外部 package repository](tutorials/external-package-repository.md), [发布 package repository](how-to/publish-package-repository.md), [Package Recipe 参考](reference/package-recipes.md) |
| 贡献者 | [Architecture](developer-guide/architecture.md), [Host 运行时契约](developer-guide/runtime-contracts.md), [Runner and context](developer-guide/runner-and-context.md), [Tool runtime](developer-guide/tool-runtime.md), [Package installer](developer-guide/package-installer.md) |

## 安装路径

正常使用时，从本仓库的 checkout 运行 managed installer：

```bash
scripts/install.sh
~/.demiurge/demiurge-agent/.venv/bin/demiurge --provider fake
```

installer 要求 `git` 和 `uv`，会创建或复用
`~/.demiurge/demiurge-agent`，运行 `uv sync`，并初始化 runtime home。

如果是 source checkout 开发，请留在仓库内使用 `uv`：

```bash
uv sync --all-groups
uv run demiurge init
uv run demiurge --provider fake
```

TUI 要求 Node.js 20 或更新版本。不带 subcommand 运行 `demiurge` 会启动 TUI。
主要 subcommands 是 `init`、`doctor`、`core`、`package`、`update`、`setup` 和
`gateway`。

## 配置解析顺序

Provider resolution 使用以下顺序：

1. CLI override，例如 `--provider <provider-id>`。
2. 选中的 runtime core manifest。
3. global fallback manifest。
4. host default provider。
5. `fake`。

Workspace resolution 使用以下顺序：

1. `--workspace <path>`。
2. `DEMIURGE_WORKSPACE`。
3. TUI launch directory。
4. 选中 core 的 `runtime.workspace`。
5. `~/.demiurge/workspace`。

## 当前 Alpha 边界

- 最新 release notes：[0.8.0](releases/0.8.0.md)。
- Python dependencies 由 host 拥有，并由 source checkout 锁定。
- Agent Slot code 运行在 host-shared Python environment 中。
- Runtime Agent Core revisions 是 `~/.demiurge/.core.git` 中的 Git commits。
- Candidate evolution 在 `.evolve/runs/<run_id>/agents` 中运行，不能自动添加
  dependencies。
- Package install 和 uninstall 是对 live agents tree 的用户触发 Git transactions；
  package recipes 不会修改 host lock file。
- Runtime layout、authoring contracts、package behavior 和 troubleshooting
  steps 在 `1.0.0` 之前仍可能变化。
