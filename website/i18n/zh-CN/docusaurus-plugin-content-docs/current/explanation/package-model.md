---
title: 包模型
description: 理解 package repositories、recipes、components、trust、install state 和 host boundaries。
---

# 包模型

Demiurge packages 是把可复用 authored-surface files 安装进 runtime Agent Cores 的 recipes。

它们不是 Python packages。它们不会安装 dependencies。它们不会编辑 `uv.lock`。如果 package 需要 locked host environment 之外的东西，它可以声明 `manual_dependencies`，这些内容会成为 human-review warnings。

## 包为什么存在

Agent Core 拥有 authored behavior：slots、tools、skills、libraries、child cores、MCP declarations 和 schedule declarations。Package 让这些 authored behavior 可以复用，而不需要手动编辑 source template。

Host 仍然拥有 runtime harness：sessions、turns、provider calls、approvals、capabilities、MCP transport、schedule execution、state、Git revisions、promotion 和 rollback。

Package management 是用户控制的 CLI workflow。Preview 是 read-only；install 和 uninstall 是 host-owned Git transactions。它不会暴露为 model-callable tool。

## 仓库

Package repository 是一个本地目录或 git checkout，包含 repository manifest 和 package recipes：

```text
repository.yaml
packages/
```

它也可以包含 component roots：

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

内置 repository 位于 source tree 的 `package-repository/`，并作为 `builtin` repository 加载。External path 和 git repositories 通过 `demiurge package repo add` 添加到 host。

Git repositories 会同步到：

```text
~/.demiurge/package-repositories/<alias>/
```

## 配方

Recipe 位于：

```text
packages/<package_id>.yaml
```

它描述 package identity、options、capability summary、manual dependency warnings 和 components。Install-time options 可以选择 components、patch component config，并在 secret 情况下被脱敏。

当前 package runtime 由 `demiurge/packages.py` 实现；内置 package recipes 位于 `package-repository/packages/`。

## 组件

支持的 component kinds：

| 类型 | 安装内容 |
| --- | --- |
| `bootstrap` | 目标 core 的 `agent/bootstrap/` 下的 bootstrap slot。 |
| `input` | `agent/input/` 下的 input slot。 |
| `output` | `agent/output/` 下的 output slot。 |
| `tool` | `agent/tools/` 下的 authored tool。 |
| `skill` | `agent/skills/` 下的 skill。 |
| `lib` | `agent/lib/` 下 package-owned helper code 或 config。 |
| `core` | package-owned runtime child core。 |
| `mcp` | 一个 MCP server declaration YAML file。 |
| `schedule` | 一个 schedule declaration YAML file。 |

只有 `bootstrap`、`input` 和 `output` components 会编辑 `agent/pipelines.yaml`。Bootstrap pipeline entries 只能是 serial。Input 和 output entries 可以是 serial 或 parallel。

`mcp` 和 `schedule` components 安装 declarations，而不是 running services。Host 仍然拥有 MCP transport、server lifecycle、schedule claims、approvals 和 schedule execution。

## Provenance 和 Drift

安装 package 会把文件写入 live runtime agents tree，并在这里记录 provenance：

```text
~/.demiurge/agents/<core-id>/packages.yaml
```

Install record 保存 package id、repository alias、repository metadata、tags、installed component targets、installed hashes、warnings 和已脱敏 options。它不会保存完整 effective config 或 secrets。`packages.yaml` 是 provenance，不是 runtime truth；runtime truth 是已提交的 agents tree。

Installation 会拒绝 target conflicts，除非该 target 已由已安装 package 持有，且 repository alias、source、target 和 effective config hash 都相同。这样 packages 可以共享完全相同的 helper components，而不会悄悄覆盖本地文件。成功 install 会运行 gates 并提交一个新的 core revision。

## 卸载状态

Uninstall 会移除 package-owned targets，并移除 `bootstrap`、`input` 和 `output` slots 的 package-owned pipeline entries。如果另一个已安装 package 仍引用相同的 shared component，该 target 会保留。

Uninstall 会更新 `packages.yaml` 并提交 runtime agents tree。如果 recorded hash 和当前文件内容不匹配，uninstall 会报告 drift 并拒绝移除，除非 caller 提供显式 destructive strategy，例如 `--force-drift`。

Uninstall 不会移除写在 package-owned targets 之外的数据。例如 memory files、generated audio、context reseed notes、provider caches 和 outbox files。

## 信任

内置 repository 是 trusted。External repositories 在 host user 确认 trust 前并不 trusted。

Trust 很重要，因为 packages 可以把可执行 Python code 安装进 host-shared Agent Core slots、tools、skills 和 libraries。Package 不能授予自己 trust。Trust 是记录在 host package repository configuration 中的 host-local decision。

## 仓库生命周期

正常 repository 工作使用交互式 manager：

```bash
uv run demiurge package
```

可脚本化的 subcommands：

```bash
uv run demiurge package repo list
uv run demiurge package repo add <path-or-git-url> --alias <alias> --trust
uv run demiurge package repo sync [alias]
uv run demiurge package repo remove <alias>
```

移除 repository source 不会卸载已经复制到 runtime cores 的 packages。不使用 `--force` 时，如果已安装 package records 仍引用该 repository，移除会被阻止。
