---
title: 演化和回滚 Core
description: 使用 host-owned 的 evolution path 和 rollback controls。
---

# 演化和回滚 Core

Demiurge 可以让 host-managed 的 `evolver` core 去编辑 active core 的 candidate copy。
Promotion 仍然由 host 拥有。

## 从 TUI 演化

在 TUI 内：

```text
/evolve Add a concise Telegram reply style input module.
```

host 会创建 candidate core，使用 candidate-scoped tools 运行 `evolver` core，检查
manifest 是否能加载，并且只在检查通过时才 promote candidate。

## 给出功能目标

好的 evolution goals 会描述行为和范围：

```text
Add an output module that emits a local Markdown artifact for long answers.
Change only agent/output and the output pipeline.
```

避免让 evolver 去编辑 host runtime code、dependencies、release files、source checkout
files 或 `.temp/` 的 goals。

## 查看版本

在 TUI 内：

```text
/versions
```

## 回滚

在 TUI 内：

```text
/rollback
```

Rollback 会通过 host version store 切回之前稳定的 core version。

## 契约

精确规则见
[/docs/reference/contracts/evolver-safe-edits](/docs/reference/contracts/evolver-safe-edits)。

evolver 可以编辑 candidate core 的 authored surface。它不能 promote、roll back、编辑
host state、修改 dependencies，或者编辑 candidate workspace 之外的文件。
