---
title: 故障排查
description: 诊断常见的 Demiurge 启动、配置、package 和 channel 故障。
---

# 故障排查

先确认精确命令、完整错误文本，以及你使用的是 managed checkout 还是
source checkout。多数入门失败来自 Node.js 版本问题、runtime-home
drift、缺少 provider secrets、无效 YAML 或意外的 workspace。

对于 managed install，请把 `uv run demiurge` 替换成：

```bash
~/.demiurge/demiurge-agent/.venv/bin/demiurge
```

## 确认命令入口

不带 subcommand 运行 `demiurge` 会启动 TUI。顶层 subcommands 是：

- `init`
- `doctor`
- `package`
- `update`
- `setup`
- `gateway`

不带其他 setup subcommand 运行 `demiurge setup` 会打开 setup wizard。

## TUI 无法启动

TUI 要求 Node.js 20 或更新版本：

```bash
node --version
```

如果缺少 Node 或版本太旧，请安装 Node.js 20 或更新版本后重试：

```bash
uv run demiurge --provider fake
```

## 找不到命令

对于 managed install，请使用 managed command path：

```bash
~/.demiurge/demiurge-agent/.venv/bin/demiurge --provider fake
```

对于 source checkout，请在仓库内通过 `uv` 运行命令：

```bash
uv run demiurge --provider fake
```

## Runtime drift 或缺少 runtime files

不写入文件，只检查：

```bash
uv run demiurge init --check
uv run demiurge doctor
```

只有在你确实想更新 runtime files 时才刷新 templates：

```bash
uv run demiurge init --refresh assistant
```

`init --refresh global` 只用于 global fallback config；只有当你确实想刷新所有 runtime
templates 时才使用 `init --refresh all`。

## Provider 或 API Key 失败

检查 setup state：

```bash
uv run demiurge setup status
```

使用 fake provider 区分 runtime 问题和 live provider 问题：

```bash
uv run demiurge --provider fake
```

如果 `fake` 可用，请检查：

- 选中的 provider profile 存在。
- provider profile 有 base URL。
- 配置的 `api_key_env` 已 export，或已写入 `~/.demiurge/.env`。
- 选中 core model 使用了预期 provider 和 `<model-name>`。

Provider resolution order 是 CLI override、core manifest、global fallback、host
default，然后是 `fake`。

## Core 或 Slot 无法加载

运行：

```bash
uv run demiurge init --check
```

然后检查受影响文件：

- `agent.yaml`
- `agent/pipelines.yaml`
- slot 的 `slot.yaml`
- slot 的 `module.py`
- tool 加载失败时，检查 authored tool `tool.yaml`

对照 [../reference/contracts/slot-modules.md](../reference/contracts/slot-modules.md)。

## Workspace 错误或 Tools 被拒绝

在 TUI 内运行：

```text
/status
```

Workspace resolution order 是 `--workspace`、`DEMIURGE_WORKSPACE`、TUI launch
directory、core `runtime.workspace`，然后是 `~/.demiurge/workspace`。

带明确 workspace 运行：

```bash
uv run demiurge --workspace /path/to/project --provider fake
```

Workspace 内仍然会应用 approvals 和 sensitive-path checks。

## Package 安装失败

先 preview：

```bash
uv run demiurge package install <package_id> --core assistant --preview
```

检查 repository 是否有：

```text
repository.yaml
packages/<package_id>.yaml
```

External repositories 必须先被 trust，才能安装本地 Agent Slot code。

## Telegram 不响应

检查：

- `channels.telegram.enabled: true`
- `DEMIURGE_TELEGRAM_BOT_TOKEN` 已设置
- `allowed_users` 或 `allowed_chats` 包含调用者
- gateway 使用预期 core 运行

```bash
uv run demiurge gateway --core assistant --provider fake
```

## 手册链接损坏

构建站点：

```bash
cd website
npm run build
```

Docusaurus 配置为：broken regular links 会报错，broken Markdown links 会警告。
