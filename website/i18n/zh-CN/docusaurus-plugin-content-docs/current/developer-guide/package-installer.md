---
title: 包安装器
description: 面向贡献者的 package repository 读取、预览、安装和卸载说明。
---

# 包安装器

Package installer 规划来自 package repositories 的用户可控 runtime-core 编辑。实际
install 和 uninstall 会作为 host-owned Git transactions 写入 live agents tree。

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
edits，按需安装 child cores，把 provenance hashes 记录到目标 core 的
`packages.yaml`，运行 host-owned gates，并提交 live agents tree。

## 卸载

Uninstall 会移除 package-owned component targets，并更新 `packages.yaml`。如果文件
已经 drift，除非 caller 提供显式 destructive strategy（例如 `--force-drift`），否则
uninstall 会拒绝移除。它不应删除写在 component-owned targets 之外的 package data。

## 边界

Installer 不会安装 dependencies，也不会编辑 `uv.lock`。请使用
`manual_dependencies` 来生成 dependency review warnings。`packages.yaml` 是
provenance，不是 runtime truth；runtime loading 来自已提交的 agents tree files。
