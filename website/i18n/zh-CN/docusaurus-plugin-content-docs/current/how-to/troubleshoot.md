---
title: 故障排查
description: 诊断常见的 Demiurge 启动、配置、package 和 channel 失败。
---

# 故障排查

先拿到精确 command 和精确 error text。大多数失败来自 runtime-home drift、缺少
secrets、无效 YAML、workspace scope 或 channel allowlist 配置。

## Runtime Drift

不写入文件，只检查：

```bash
uv run demiurge init --check
uv run demiurge doctor
```

只有在你确实想更新 runtime files 时才刷新 templates：

```bash
uv run demiurge init --refresh assistant
```

## 缺少 Provider 或 API Key

检查 setup state：

```bash
uv run demiurge setup status
```

使用 fake provider，把 runtime 问题和 provider 问题分开：

```bash
uv run demiurge --provider fake
```

## Core 无法加载

运行：

```bash
uv run demiurge init --check
```

然后检查受影响文件：

- `agent.yaml`
- `agent/input/pipeline.yaml`
- `agent/output/pipeline.yaml`
- `agent/bootstrap/pipeline.yaml`
- slot 的 `slot.yaml`
- slot 的 `module.py`

对照 [../reference/contracts/slot-modules.md](../reference/contracts/slot-modules.md)。

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

External repositories 必须先被 trust，才能安装本地 code slots。

## File 或 Terminal Tool 被拒绝

检查 workspace：

```text
/status
```

带明确 workspace 运行：

```bash
uv run demiurge --workspace /path/to/project
```

Approvals 和 sensitive-path checks 仍然生效。

## Telegram 不响应

检查：

- `channels.telegram.enabled: true`
- `DEMIURGE_TELEGRAM_BOT_TOKEN` 已设置
- `allowed_users` 或 `allowed_chats` 包含调用者
- gateway 使用预期 core 运行

```bash
uv run demiurge gateway --core assistant --provider fake
```

## Website 或手册链接损坏

构建站点：

```bash
cd website
npm run build
```

Docusaurus 配置为：普通 broken links 会报错，broken Markdown links 会警告。
