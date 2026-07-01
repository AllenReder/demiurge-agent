---
title: 包安装器
description: 面向贡献者的 package repository 读取、预览、安装和卸载说明。
---

# 包安装器

Package installer 管理用户可控的 runtime-core 编辑，这些编辑来自 package
repositories。

## 读取

Loader 会读取：

```text
repository.yaml
packages/*.yaml
```

它会验证 repository identity、package ids、options、components、conditions 和
component source paths。

## 预览

Preview 会解析：

- package reference
- selected options
- included components
- config writes
- pipeline edits
- warnings
- target paths

Preview 期间不得写入 runtime files。

## 安装

Install 会复制 component files，在需要时写入 component `config.yaml`，应用 pipeline
edits，按需安装 child cores，并把状态记录到目标 core 的 `packages.yaml` 中。

## 卸载

Uninstall 会移除 package-owned component targets，并更新 `packages.yaml`。它不应删除
写在 component-owned targets 之外的 package data。

## 边界

Installer 不会安装 dependencies，也不会编辑 `uv.lock`。请使用
`manual_dependencies` 来生成 dependency review warnings。
