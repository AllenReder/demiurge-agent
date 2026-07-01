---
slug: /
sidebar_position: 0
title: Demiurge 手册
description: 面向运行 Demiurge、编写 Agent Core 和构建 package repository 的中文核心手册。
---

# Demiurge 手册

Demiurge 用于打造文件化、可自进化的 Agent Core。host 拥有 runtime
harness。Agent Core 拥有 authored surface。Package repository 会把可复用能力安装进
runtime core。

本手册按 Diataxis 文档模型组织：

- **教程**带你从零完成一条可工作的路径。
- **操作指南**解决具体任务。
- **解释**说明系统为什么这样设计。
- **参考**定义精确的命令、schema 和 contract。

参考里的 contract 页面也可以作为只读上下文提供给 `evolver` core，用于辅助它
修改 Agent Core。

## 从这里开始

| 目标 | 页面 |
| --- | --- |
| 在本地启动 Demiurge | [tutorials/quick-start.md](tutorials/quick-start.md) |
| 做一次安全的 Agent Core 修改 | [tutorials/customize-agent-core.md](tutorials/customize-agent-core.md) |
| 创建外部 package repository | [tutorials/external-package-repository.md](tutorials/external-package-repository.md) |
| 配置真实模型 provider | [how-to/configure-provider.md](how-to/configure-provider.md) |
| 理解 host/core 边界 | [explanation/host-and-agent-core.md](explanation/host-and-agent-core.md) |
| 阅读稳定 authored-surface 规则 | [reference/contracts/authored-surface.md](reference/contracts/authored-surface.md) |

## 阅读路径

Alpha 用户建议阅读：

1. [快速开始](tutorials/quick-start.md)
2. [配置 provider](how-to/configure-provider.md)
3. [选择 workspace](how-to/choose-workspace.md)
4. [故障排查](how-to/troubleshoot.md)

Agent Core 作者建议阅读：

1. [Host 和 Agent Core](explanation/host-and-agent-core.md)
2. [修改 Agent Core](tutorials/customize-agent-core.md)
3. [编写 slot module](how-to/write-slot-module.md)
4. [Authored surface contract](reference/contracts/authored-surface.md)

Package 和 repository 作者建议阅读：

1. [Package model](explanation/package-model.md)
2. [创建外部 package repository](tutorials/external-package-repository.md)
3. [安装 package](how-to/install-packages.md)
4. [Package repository contract](reference/contracts/package-repositories.md)

贡献者建议阅读：

1. [Architecture](developer-guide/architecture.md)
2. [Runner and context](developer-guide/runner-and-context.md)
3. [Tool runtime](developer-guide/tool-runtime.md)
4. [Package installer](developer-guide/package-installer.md)

## 当前 Alpha 边界

- Python dependencies 由 host 拥有，并由 source checkout 锁定。
- Agent Core code slot 运行在 host-shared Python environment 中。
- Candidate Agent Core evolution 不能自动添加 dependencies。
- Package recipes 会把文件安装进 runtime cores；它们不会修改 host lock file。
- Release notes 保留在 [releases/](releases/0.4.0.md) 下。
