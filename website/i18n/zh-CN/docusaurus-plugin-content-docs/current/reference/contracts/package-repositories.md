---
title: Package Repository 规则
description: 外部 package repositories 和 package recipes 的稳定规则。
---

# Package Repository 规则

Package repository 会把可复用 authored-surface files 安装进 runtime Agent Core。
它们必须可以安全地 inspect、preview 和 uninstall。

## Repository Root

必需：

```text
repository.yaml
packages/
```

可选 component roots：

```text
bootstrap/
input/
output/
tool/
skill/
lib/
core/
```

## `repository.yaml`

```yaml
schema_version: 1
id: community
name: Community Packages
summary: Shared Demiurge package recipes.
```

`id` 必须稳定。本地用户添加 repository 时，可以分配不同 alias。

## Recipe Rules

每个 recipe 位于：

```text
packages/<package_id>.yaml
```

规则：

- Package ids 在 repository 内唯一。
- Component ids 在 recipe 内唯一。
- Component sources 保持在 repository 内。
- Component sources 不能是 symlinks。
- Pipeline edits 只允许用于 bootstrap、input 和 output components。
- Bootstrap pipeline edits 只能是 serial。
- `manual_dependencies` 只是 warnings。
- Recipes 不编辑 host dependency files。

## Trust Rule

安装本地可执行 code 前，必须先信任 external repositories：

```bash
uv run demiurge package repo add ./local-packages --alias local --trust
```

Trust 是 host-local 的。Package 不能让自己变成 trusted。

## Secret Rule

Secret options 使用 `type: secret`。Secret values 可以写入已安装 component config，
但 `packages.yaml` 只记录 `<redacted>`。

## 验证

```bash
uv run demiurge package repo list
uv run demiurge package list --repo <alias>
uv run demiurge package install <alias>/<package_id> --core assistant --preview
uv run demiurge init --check
```
