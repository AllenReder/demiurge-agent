---
title: 发布 Package Repository
description: 为其他 Demiurge 用户准备、验证并共享本地或 git package repository。
---

# 发布 Package Repository

当一个 package repository 已经能在本地工作，并且你想让其他 Demiurge 用户把它添加为 path 或 git source 时，使用本指南。

Package repositories 分发 authored-surface files。它们不是 Python packages，不会安装 dependencies，也不会编辑 host `uv.lock`。

如果你还没有创建 repository，请先阅读
[创建外部 Package Repository](../tutorials/external-package-repository.md)。
如果你需要设计 `packages/<package_id>.yaml`，请阅读 [编写 Package Recipe](write-package-recipe.md)。
精确 schema 规则见
[Package Repository Contract](../reference/contracts/package-repositories.md)
和 [Package Recipe 参考](../reference/package-recipes.md)。

## 1. 准备仓库根目录

一个可分发 repository 必须包含：

```text
repository.yaml
packages/
```

它还可以包含 component roots：

```text
bootstrap/
input/
output/
tool/
skill/
lib/
core/
mcp/
schedule/
```

在 `repository.yaml` 中使用稳定的 repository id：

```yaml
schema_version: 1
id: community
name: Community Packages
summary: Shared Demiurge package recipes.
```

Repository id 用来标识这个 source。用户添加 repository 时，仍然可以选择不同的本地 alias。

## 2. 让 Package 文件保持集中

每个 package recipe 位于：

```text
packages/<package_id>.yaml
```

每个 component source 位于对应的 component root 下。例如，一个 input package 可以使用：

```text
packages/reply_style.yaml
input/reply_style/
  slot.yaml
  module.py
```

Recipe 把 package id、component source 和 target 连接起来：

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

`source` 值指向 repository 内的 `input/reply_style/`。`target` 值相对于安装该 package 的 runtime core。

## 3. 本地验证

把 repository 作为 trusted local path 添加：

```bash
uv run demiurge package repo add ~/demiurge-packages \
  --alias local \
  --trust
```

检查 Demiurge 能读取它：

```bash
uv run demiurge package repo list
uv run demiurge package list --repo local
```

写入 runtime core 之前先预览 package：

```bash
uv run demiurge package install local/reply_style \
  --core assistant \
  --preview
```

预览结果正确后再安装：

```bash
uv run demiurge package install local/reply_style --core assistant
```

检查目标 runtime core 仍能加载：

```bash
uv run demiurge init --check
```

用于自动化检查时，repository 和 package commands 也支持 machine-readable output：

```bash
uv run demiurge package repo list --json
uv run demiurge package list --repo local --json
uv run demiurge package install local/reply_style --core assistant --preview --json
```

## 4. 共享仓库

对于本地团队 path，用户可以直接添加目录：

```bash
uv run demiurge package repo add /path/to/demiurge-packages \
  --alias team \
  --trust
```

对于 git repository，发布 repository root，并告诉用户使用哪个 ref：

```bash
uv run demiurge package repo add https://github.com/user/demiurge-packages.git \
  --alias community \
  --ref v0.1.0 \
  --trust
```

用 tag 或 commit 表示稳定 release。只有当用户明确需要移动中的 source 时，才使用 `main` 这样的 branch。

如果 package repository 位于更大的 git repository 里的子目录，记录这个 subdirectory：

```bash
uv run demiurge package repo add https://github.com/user/community.git \
  --alias community \
  --ref v0.1.0 \
  --subdir demiurge-packages \
  --trust
```

Git repositories 会同步到：

```text
~/.demiurge/package-repositories/<alias>/
```

## 5. 发布更新

发布更新前：

- 保持现有 package ids 稳定，除非用户应该把它视为不同 package。
- 保持 component targets 稳定，除非 release notes 明确告诉用户如何迁移本地 runtime cores。
- 重新运行本地 repository list、package list、install preview 和 `uv run demiurge init --check` 检查。
- 发布新的 git commit 或 tag。

用户用下面的命令刷新已配置的 git repository：

```bash
uv run demiurge package repo sync community
```

Sync 会更新未来 list 和 install commands 使用的 repository source。它不会更新已经安装到 runtime cores 的文件。

要把已变更 package 应用到现有 runtime core，用户应该先预览变更，卸载已安装 package，然后再次安装：

```bash
uv run demiurge package uninstall community/reply_style --core assistant --preview
uv run demiurge package uninstall community/reply_style --core assistant
uv run demiurge package install community/reply_style --core assistant --preview
uv run demiurge package install community/reply_style --core assistant
```

Uninstall 会移除 package-owned targets 和 package-owned pipeline entries。它不会移除 package 写在 package-owned targets 之外的数据。

## Security 和 Dependency 边界

External repositories 可以把可执行 Python slot code、authored tools、skills、libraries、child cores、MCP declarations 和 schedule declarations 安装进 runtime Agent Cores。

Trust 是本地 host decision。Package 不能让自己变成 trusted。

不要在 package recipes 或 component config 中发布 secrets。使用 `type: secret` 的 package options、component 文档说明的环境变量，或者由安装用户拥有的本地 runtime configuration。

Package recipes 仍然不能安装 Python dependencies，也不能编辑 `uv.lock`。`manual_dependencies` 只能作为给人工 review 的 warnings。
