---
title: 管理包仓库
description: 添加、列出、同步和移除内置、本地路径与 git 包仓库。
---

# 管理包仓库

Package repositories 是 host-level 的 package recipes 来源。本页面面向在自己 host 上添加、同步和移除 repositories 的用户。如果你正在为其他用户创建 repository，请阅读 [发布 Package Repository](publish-package-repository.md)。

内置 repository 默认可用。只有在信任外部 repository 的代码时，才添加外部 repositories。

最简单的路径是交互式 package manager：

```bash
uv run demiurge package
```

打开 **Repos** 可以列出 repositories、添加 path 或 git source、同步 git caches，或移除已配置 source。

当你需要明确命令时，使用下面的 subcommands。

## 列出仓库

```bash
uv run demiurge package repo list
```

机器可读输出：

```bash
uv run demiurge package repo list --json
```

列表会显示每个 repository alias、source type、package count、root 或 git ref、已知 commit，以及 readiness status。

## 添加本地路径仓库

Path repository 指向一个已有的本地目录：

```bash
uv run demiurge package repo add ~/demiurge-packages \
  --alias local \
  --trust
```

当 package repository 位于该 path 之下的子目录时，使用 `--subdir`：

```bash
uv run demiurge package repo add ~/workspace/community \
  --alias community \
  --subdir demiurge-packages \
  --trust
```

非交互式添加外部 repository 时必须使用 `--trust`。如果没有它，交互式 manager 会请求确认。

## 添加 Git 仓库

```bash
uv run demiurge package repo add https://github.com/user/demiurge-packages.git \
  --alias community \
  --ref main \
  --trust
```

Git repositories 会同步到：

```text
~/.demiurge/package-repositories/<alias>/
```

用 `--ref` 指定 branch、tag 或 commit。当 git checkout 的根目录下还有一层 package repository 子目录时，使用 `--subdir`。

## 同步仓库

同步所有已配置 repositories：

```bash
uv run demiurge package repo sync
```

同步一个 repository：

```bash
uv run demiurge package repo sync community
```

对于 git repositories，sync 会 fetch remote updates 并 checkout 已配置的 ref。对于 path repositories，sync 会验证当前目录。

## 从仓库安装

添加 repository 后，列出其中的 packages：

```bash
uv run demiurge package list --repo community
```

用带 repository 前缀的 package ref 安装：

```bash
uv run demiurge package install community/reply_style --core assistant --preview
uv run demiurge package install community/reply_style --core assistant
```

当不同 repositories 包含相同 package id 时，带 repository 前缀的 refs 可以避免歧义。

## 移除仓库

```bash
uv run demiurge package repo remove community
```

内置 repository 不能移除。

如果已安装 package records 仍引用该 repository，移除会被阻止。先卸载这些 packages。只有当你有意移除 repository source、同时保留 runtime cores 中已安装的 package records 时，才使用 `--force`：

```bash
uv run demiurge package repo remove community --force
```

移除 git repository 也会移除它在 `~/.demiurge/package-repositories/<alias>/` 下的 cache。移除 path repository 只会移除 host configuration entry。

## Trust 边界

External repositories 可以把可执行 Python slot code、authored tools、skills、libraries、child cores、MCP declarations 和 schedule declarations 安装进 runtime Agent Cores。

Trust 是本地 host policy。Repository 或 package 不能让自己变成 trusted。添加外部 source 前，先 review `repository.yaml`、`packages/` 下的 package recipes，以及 component source files。

Packages 仍然不会安装 Python dependencies，也不会编辑 `uv.lock`。如果 recipe 声明了 `manual_dependencies`，把这些字符串当作需要人工 review 的 warnings。
