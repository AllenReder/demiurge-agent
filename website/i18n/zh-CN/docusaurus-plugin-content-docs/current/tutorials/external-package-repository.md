---
title: 创建外部 Package Repository
description: 构建一个小型 trusted package repository，并把一个 input component 安装到 runtime core。
---

# 创建外部 Package Repository

本教程会在 Demiurge source checkout 之外创建一个本地 package repository。这个
repository 会把一个 input module 安装到 runtime `assistant` core。

Package repository 用来复用 Agent Core components。它们会把文件安装进 runtime
cores；不会修改 source templates，也不会安装 Python dependencies。

Packages 可以组合 Agent Slots、tools、skills、libraries 和 child cores。本教程安装
一个 input slot。

## 1. 创建 Repository

选择一个本地路径：

```bash
mkdir -p ~/demiurge-packages/packages
mkdir -p ~/demiurge-packages/input/reply_style
```

添加 `repository.yaml`：

```yaml
schema_version: 1
id: local_examples
name: Local Demiurge Examples
summary: Local example packages for testing.
```

## 2. 添加 Input Component

创建 `input/reply_style/slot.yaml`：

```yaml
entrypoint: module:process
description: "Adds a package-provided reply style hint."
failure_policy: soft
capabilities: []
```

创建 `input/reply_style/module.py`：

```python
def process(ctx):
    ctx.input.add_context("Package hint: answer with direct, concrete steps.", role="system")
```

## 3. 添加 Package Recipe

创建 `packages/reply_style.yaml`：

```yaml
schema_version: 1
id: reply_style
name: Reply Style
summary: Add a package-provided reply style hint.
tags:
  - style
components:
  - id: reply_style_input
    kind: input
    source: reply_style
    target: agent/input/reply_style
    pipeline:
      group: serial
      append: true
```

`source` path 是 `input/` 下的 repository-relative 路径。`target` path 是
runtime-core-relative 路径。安装 package 会复制 component 目录并更新目标
core 的 `agent/pipelines.yaml`。

## 4. Trust 并添加 Repository

```bash
uv run demiurge package repo add ~/demiurge-packages --alias local --trust
uv run demiurge package repo list
```

Trust 必须显式授予，因为 repository 可以把可执行 Python slot code 安装进
runtime core。

## 5. Preview 并安装

```bash
uv run demiurge package list --repo local
uv run demiurge package install local/reply_style --core assistant --preview
uv run demiurge package install local/reply_style --core assistant
```

安装会修改：

```text
~/.demiurge/agents/assistant/
```

安装状态记录在：

```text
~/.demiurge/agents/assistant/packages.yaml
```

## 6. 验证

```bash
uv run demiurge init --check
uv run demiurge --provider fake
```

如果 package 无法加载，阅读精确错误，并对照
[../reference/contracts/package-repositories.md](../reference/contracts/package-repositories.md)。

## 7. 卸载

```bash
uv run demiurge package uninstall local/reply_style --core assistant --preview
uv run demiurge package uninstall local/reply_style --core assistant
```

Uninstall 会移除 package-owned component targets，并更新 `packages.yaml`。它不会
删除 component 在 owned targets 之外创建的数据文件。
