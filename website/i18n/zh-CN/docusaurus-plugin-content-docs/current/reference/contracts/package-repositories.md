---
title: 包仓库契约
description: External package repositories 和 package recipes 的稳定规则。
---

# 包仓库契约

本 contract 描述 Demiurge 可以 inspect、trust、preview、install、sync 和 uninstall 的 repository 形状。

Implementation source of truth 是 `demiurge/packages.py`。内置 repository 是 `package-repository/`。

## 仓库根目录

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
mcp/
schedule/
```

## `repository.yaml`

```yaml
schema_version: 1
id: community
name: Community Packages
summary: Shared Demiurge package recipes.
```

`id` 必须稳定。本地用户添加 repository 时可以分配不同 alias。

## 包配方

每个 recipe 位于：

```text
packages/<package_id>.yaml
```

规则：

- `schema_version` 必须是 `1`。
- Package ids 在 repository 内唯一。
- Component ids 在 recipe 内唯一。
- Component `kind` 必须是 `bootstrap`、`input`、`output`、`tool`、`skill`、`lib`、`core`、`mcp` 或 `schedule` 之一。
- Component sources 必须留在 repository 内。
- Component sources 不能是 symlinks。
- 已移除的 v1 fields 会被拒绝，例如 `slots`、`tools`、`files`、`config_defaults` 和 `metadata`。
- `manual_dependencies` 只是 warnings。
- Recipes 不会安装 Python dependencies，也不会编辑 host dependency files。

## Pipeline 规则

Pipeline edits 只对 `bootstrap`、`input` 和 `output` components 有效。

规则：

- `bootstrap` 只支持 `group: serial`。
- `input` 和 `output` 支持 `group: serial` 与 `group: parallel`。
- Pipeline entry 必须且只能声明 `append`、`before` 或 `after` 之一。
- 如果 `before` 或 `after` target 缺失，install 会失败。
- 如果 target pipeline 已经包含 package slot id，install 会失败。
- Uninstall 会移除 package-owned pipeline entries。

## Component Targets

Directory components 会安装到 runtime-core-relative targets：

| 类型 | 默认 target root |
| --- | --- |
| `bootstrap` | `agent/bootstrap/` |
| `input` | `agent/input/` |
| `output` | `agent/output/` |
| `tool` | `agent/tools/` |
| `skill` | `agent/skills/` |
| `lib` | `agent/lib/` |

`core` components 会创建或更新由 `target_core_id` 命名的 package-owned runtime core。

当 unmanaged target 已存在时，install 会失败。只有当另一个已安装 package 拥有相同 repository alias、source、target 和 effective config hash 时，才允许 shared targets。

## Manifest 文件组件

`mcp` 和 `schedule` components 会各自安装一个 YAML declaration file。

默认：

- `mcp` 使用 target core 的 `slots.mcp` root；未设置时使用 `agent/mcp`。
- `schedule` 使用 target core 的 `slots.schedules` root；未设置时使用 `agent/schedules`。

规则：

- Source files 位于 `mcp/` 或 `schedule/` 下。
- Targets 必须是 YAML files。
- Targets 必须直接位于 declaration root 内。
- Component `config` 会用 package options 渲染，并在 validation 前作为 manifest overlay 应用。
- Installed files 会按 schema defaults 标准化。

MCP 和 schedule packages 安装 declarations，而不是 running servers 或 claimed jobs。Host 拥有 MCP transport、server lifecycle、schedule claims、approvals 和 execution。

示例：

```yaml
schema_version: 1
id: docs_and_daily
components:
  - id: docs
    kind: mcp
    source: docs.yaml
    config:
      url: ${options.url}
  - id: daily
    kind: schedule
    source: daily.yaml
    config:
      schedule: "0 9 * * *"
      prompt: "Write a daily summary."
```

## 选项和密钥

支持的 option types 是 `string`、`bool`、`choice`、`path` 和 `secret`。

Secret values 使用 `type: secret`。Secret values 可能会写入 installed component config，但 `packages.yaml` 只记录 `<redacted>`。

未知的 script-supplied option ids 会被拒绝。Required options 必须解析为非空值。

## Trust 规则

External repositories 必须先被 trust，才能安装：

```bash
uv run demiurge package repo add ./local-packages --alias local --trust
```

交互式 manager 会请求 trust confirmation。非交互式 external adds 需要 `--trust`。

Trust 是 host-local。Package 不能让自己 trusted。

Path repositories 从配置的 path 读取。Git repositories 会同步到：

```text
~/.demiurge/package-repositories/<alias>/
```

## 安装和卸载契约

Preview 必须是 read-only。Install 会写入 package-owned runtime core targets、slot components 的 pipeline entries，并在这里写入 package provenance record：

```text
~/.demiurge/agents/<core-id>/packages.yaml
```

`packages.yaml` 记录 installed targets 和 hashes；它是 provenance，不是 runtime truth。成功 install 会运行 host-owned gates 并提交 live agents tree。

Uninstall 会移除 package-owned targets 和 pipeline entries，除非另一个已安装 package 仍引用相同 shared component。然后它会更新 `packages.yaml` 并提交 live agents tree。若 package-owned files 已 drift，uninstall 会拒绝移除，除非 caller 提供显式 destructive strategy，例如 `--force-drift`。

Package-owned targets 之外的数据不属于 uninstall contract。

## 验证

验证 repository：

```bash
uv run demiurge package repo list
uv run demiurge package list --repo <alias>
```

预览 package：

```bash
uv run demiurge package install <alias>/<package_id> --core assistant --preview
```

安装后检查 runtime：

```bash
uv run demiurge core check
```
