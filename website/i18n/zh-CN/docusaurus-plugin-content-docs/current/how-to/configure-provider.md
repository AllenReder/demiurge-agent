---
title: 配置 Provider
description: 创建 provider profile，选择 core 使用的 model，并验证真实 provider。
---

# 配置 Provider

在 TUI 能够本地启动之前，请使用 fake provider。之后再在 host config 中添加真实
provider profile，并让 runtime core 指向一个 model。

对于 managed install，请把 `uv run demiurge` 替换成：

```bash
~/.demiurge/demiurge-agent/.venv/bin/demiurge
```

## 1. 打开向导

不带 setup subcommand 运行 setup：

```bash
uv run demiurge setup
```

wizard 可以创建 provider profiles、把 secrets 写入 `~/.demiurge/.env`、选择
host default provider，并设置 core model。

## 2. 检查当前状态

修改前后都可以使用：

```bash
uv run demiurge setup status
uv run demiurge setup status --json
```

`setup status` 报告 secret sources，不报告 secret values。

## 3. 添加 Provider Profile

如果你的 provider 匹配 built-in preset，请从该 preset 开始：

```bash
uv run demiurge setup providers add <provider-id> \
  --preset <preset-id> \
  --api-key-env <API_KEY_ENV> \
  --set-default
```

如果你的 provider 是自定义 OpenAI-compatible endpoint，请提供 base URL：

```bash
uv run demiurge setup providers add <provider-id> \
  --base-url https://<provider-host>/v1 \
  --api-key-env <API_KEY_ENV> \
  --set-default
```

Provider profile 也会选择 `api_mode`，用于控制 host-owned wire protocol
adapter。内置 OpenAI-compatible presets 默认使用 `openai-chat`。只有 endpoint
使用 Anthropic Messages API 时才使用 `anthropic-messages`：

```bash
uv run demiurge setup providers add anthropic \
  --api-mode anthropic-messages \
  --base-url https://api.anthropic.com/v1 \
  --api-key-env ANTHROPIC_API_KEY \
  --set-default
```

在 shell 中 export secret，或把它存入 `~/.demiurge/.env`：

```bash
export <API_KEY_ENV>=<api-key>
```

如果要让 setup 把提供的 key 写入 runtime `.env` 文件：

```bash
uv run demiurge setup providers add <provider-id> \
  --preset <preset-id> \
  --api-key "$<API_KEY_ENV>" \
  --write-env \
  --set-default
```

## 4. 设置 Core Model

设置 `assistant` core 使用的 model：

```bash
uv run demiurge setup model set \
  --core assistant \
  --provider <provider-id> \
  --model <model-name>
```

使用你的 provider 预期的 model name。不要提交 secrets 或本地 provider choices，除非你
确实希望共享它们。

## 5. 测试并运行

运行显式 provider test：

```bash
uv run demiurge setup providers test <provider-id> --model <model-name>
```

然后用该 provider 启动 TUI：

```bash
uv run demiurge --provider <provider-id>
```

如果启动失败，确认 fake provider 仍然可用：

```bash
uv run demiurge --provider fake
```

## Provider 解析顺序

Demiurge 按以下顺序选择 provider：

1. CLI override，例如 `--provider <provider-id>`。
2. 选中的 runtime core manifest。
3. global fallback manifest。
4. host default provider。
5. `fake`。

当你需要区分 runtime 问题和 live provider 问题时，请使用 `--provider fake`。

## 常用命令

```bash
uv run demiurge setup providers list
uv run demiurge setup providers show <provider-id>
uv run demiurge setup providers edit <provider-id> --base-url https://<provider-host>/v1
uv run demiurge setup providers set-default <provider-id>
uv run demiurge setup providers remove <provider-id>
uv run demiurge setup timezone set <IANA-timezone>
uv run demiurge setup timezone clear
```

## 边界和 Secrets

Provider profiles 是 host-owned configuration。Agent Core 可以在 `agent.yaml`
中声明 model defaults，但 Agent Slots 不应该直接构造 provider requests 或读取
secrets。Host 会解析 profile、选择 `api_mode`、把内部 `LLMRequest` 转成
provider-native payload，并把 response 归一回 `LLMResponse`。API keys 优先使用
环境变量或 `~/.demiurge/.env`。
