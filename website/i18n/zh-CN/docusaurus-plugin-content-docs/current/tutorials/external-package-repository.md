---
title: 创建外部包仓库
description: 构建一个小型 trusted package repository，并把一个 input component 安装到 runtime core。
---

# 创建外部包仓库

本教程会在 Demiurge source checkout 之外创建一个本地 package repository。你将添加一个 input slot package，信任该 repository，预览安装，然后再次卸载。

Package repositories 分发 authored-surface files。它们不会安装 Python dependencies，也不会修改 host `uv.lock`。

## 1. 创建仓库根目录

选择一个本地路径：

```bash
mkdir -p ~/demiurge-packages/packages
mkdir -p ~/demiurge-packages/input/reply_style
```

创建 `~/demiurge-packages/repository.yaml`：

```yaml
schema_version: 1
id: local_examples
name: Local Demiurge Examples
summary: Local example packages for testing.
```

`repository.yaml` 标识 repository。把它添加到 host 时，本地 alias 仍然可以不同。

## 2. 添加 Input Slot

创建 `~/demiurge-packages/input/reply_style/module.py`：

```python
def process(ctx):
    ctx.input.add_context(
        "Package hint: answer with direct, concrete steps.",
        role="system",
        write_history=False,
    )
```

创建 `~/demiurge-packages/input/reply_style/slot.yaml`：

```yaml
entrypoint: module:process
failure_policy: soft
history_policy: transient
capabilities: []
description: Adds a package-provided reply style hint.
```

Input slots 会在 provider call 前运行。这个示例会为每个 turn 添加一条低优先级 system context hint。

## 3. 添加包配方

创建 `~/demiurge-packages/packages/reply_style.yaml`：

```yaml
schema_version: 1
id: reply_style
name: Reply Style
summary: Add a package-provided reply style hint.
tags:
  - input
  - style
components:
  - id: reply_style_input
    kind: input
    source: reply_style
    target: agent/input/reply_style
    pipeline:
      group: serial
      append: true
capabilities: []
```

`source` 值指向 repository 内的 `input/reply_style/`。`target` 值相对于 runtime core。因为这是 input slot，recipe 必须包含 pipeline placement。

## 4. 添加并信任仓库

```bash
uv run demiurge package repo add ~/demiurge-packages --alias local --trust
uv run demiurge package repo list
```

Repositories 可以把可执行本地代码安装进 host-shared Agent Core slots，因此必须信任。

你也可以使用交互式 manager：

```bash
uv run demiurge package
```

打开 **Repos**，添加 path，review 检测到的 repository metadata，然后确认 trust。

## 5. 预览并安装

列出新 repository 中的 packages：

```bash
uv run demiurge package list --repo local
```

预览安装：

```bash
uv run demiurge package install local/reply_style --core assistant --preview
```

安装：

```bash
uv run demiurge package install local/reply_style --core assistant
```

安装会写入 active runtime core：

```text
~/.demiurge/agents/assistant/
```

它会把 input slot 复制到：

```text
~/.demiurge/agents/assistant/agent/input/reply_style/
```

它还会把 `reply_style` 追加到 input pipeline，并在这里记录 package：

```text
~/.demiurge/agents/assistant/packages.yaml
```

## 6. 验证

检查已安装 package state：

```bash
uv run demiurge package list --core assistant
```

检查 runtime core 仍能加载：

```bash
uv run demiurge init --check
```

运行一个 fake-provider turn：

```bash
uv run demiurge --provider fake
```

如果 package 加载失败，将 repository 与 [Package Repository Contract](../reference/contracts/package-repositories.md) 对比，并将 recipe 与 [Package Recipe Reference](../reference/package-recipes.md) 对比。

## 7. 卸载

预览移除：

```bash
uv run demiurge package uninstall local/reply_style --core assistant --preview
```

卸载：

```bash
uv run demiurge package uninstall local/reply_style --core assistant
```

Uninstall 会移除 `agent/input/reply_style/`，移除 package-owned pipeline entry，并更新 `packages.yaml`。它不会删除 package 在 package-owned targets 之外创建的文件。
