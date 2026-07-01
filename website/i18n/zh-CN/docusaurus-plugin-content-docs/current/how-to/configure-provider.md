---
title: 配置 Provider
description: 创建 provider profiles，并选择 core 使用的模型。
---

# 配置 Provider

在 runtime 本地可用之前，先使用 fake provider。确认本地路径跑通后，再在 host
config 中配置真实 provider profile。

## 交互式 Setup

```bash
uv run demiurge setup
```

Setup 流程可以创建 provider profiles、把 secrets 写入 `~/.demiurge/.env`、选择
default provider，并设置 core model。

检查结果：

```bash
uv run demiurge setup status
uv run demiurge setup status --json
```

## 脚本式 Setup

创建 OpenAI profile，并把它设为 host default：

```bash
uv run demiurge setup providers add openai --preset openai --set-default
```

为 `assistant` core 设置模型：

```bash
uv run demiurge setup model set --core assistant --provider openai --model gpt-5.5
```

使用这个 provider 运行：

```bash
uv run demiurge --provider openai
```

## Secrets

优先使用环境变量或 `~/.demiurge/.env` 保存 secrets：

```bash
uv run demiurge setup providers add openai \
  --preset openai \
  --api-key-env OPENAI_API_KEY \
  --set-default
```

本地测试时，可以让 setup 把提供的 key 写入 `.env`：

```bash
uv run demiurge setup providers add openai \
  --preset openai \
  --api-key "$OPENAI_API_KEY" \
  --write-env \
  --set-default
```

`/status` 和 `setup status --json` 只报告 secret sources，不显示 secret values。

## 常用命令

```bash
uv run demiurge setup providers list
uv run demiurge setup providers edit openai --base-url https://api.openai.com/v1
uv run demiurge setup providers test openai --model gpt-5.5
uv run demiurge setup providers set-default openai
uv run demiurge setup model set --core assistant --provider openai --model gpt-5.5
uv run demiurge setup timezone set Asia/Shanghai
uv run demiurge setup timezone clear
```

## 边界

Provider profiles 是 host-owned configuration。Agent Core 可以在 `agent.yaml`
中声明 model defaults，但 code slot 不应该直接构造 provider requests 或读取
secrets。
