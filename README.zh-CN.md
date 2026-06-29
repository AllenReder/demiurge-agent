# demiurge

`demiurge` 是一个 local-first 的 Python agent harness：宿主负责 runtime loop、工具、审批、状态、delivery 和版本管理，而每个 agent core 保持为可编写的 `agent.yaml + agent/` 表面。

状态：**alpha / developer preview**。API、runtime 布局和 authoring contract 仍可能变化。

English README: [README.md](README.md)

## 快速开始

默认推荐 managed install：

```bash
scripts/install.sh
~/.demiurge/demiurge-agent/.venv/bin/demiurge --provider fake
```

这会创建：

- managed checkout：`~/.demiurge/demiurge-agent`
- live runtime cores：`~/.demiurge/agents`
- 默认工具 workspace：`~/.demiurge/workspace`

后续更新 managed checkout：

```bash
~/.demiurge/demiurge-agent/.venv/bin/demiurge update
```

`demiurge update` 会更新代码和依赖，然后运行只读 runtime drift check。它不会覆盖 live agent cores。

## Agent Cores 和 IO

agent core 是 `~/.demiurge/agents/<core>/` 下的 authored surface：`agent.yaml` 加一个 `agent/` 目录。宿主负责执行、provider 调用、工具、审批、状态、session 和 delivery；core 声明 instructions、skills、channels 和可选 code slots。

IO modules 是 core-local 的 input shaping 和 output delivery 扩展点。它们让 core 能适配 channel input、格式化回复、产生本地 artifact，或路由 output，同时仍经过宿主负责的 capabilities 和 approvals。

完整 authoring model 见 [docs/agents.md](docs/agents.md)、[docs/agent-core-authoring.md](docs/agent-core-authoring.md) 和 [docs/channels.md](docs/channels.md)。

## 配置真实 Provider

demiurge 使用 OpenAI-compatible Chat Completions 接口：

```bash
export DEMIURGE_MODEL_NAME="gpt-5.4-mini"
export DEMIURGE_BASE_URL="https://api.openai.com/v1"
export DEMIURGE_API_KEY="..."
~/.demiurge/demiurge-agent/.venv/bin/demiurge --provider openai
```

也可以用临时 CLI 覆盖：

```bash
uv run demiurge --provider openai --model deepseek-v4-flash --base-url https://example.com/v1 --api-key "$DEMIURGE_API_KEY"
```

真实密钥应放在环境变量里。`/status` 只显示密钥来源，不显示密钥值。

## Telegram Gateway

在目标 core 中启用 Telegram：

```yaml
channels:
  telegram:
    enabled: true
    bot_token_env: DEMIURGE_TELEGRAM_BOT_TOKEN
```

然后运行：

```bash
export DEMIURGE_TELEGRAM_BOT_TOKEN="..."
demiurge gateway --core assistant
```

Telegram 默认拒绝未授权访问。私聊需要把数字 `from.id` 加入 `allowed_users`；群聊需要同时允许 user id 和 chat id。

## 开发者工作流

源码 checkout 开发：

```bash
uv sync --all-groups
uv run pytest
uv run demiurge --provider fake
```

如果修改 TUI：

```bash
cd ui-tui
npm ci
npm test -- --run
npm run typecheck
npm run build
cd ..
```

完整验证流程见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 文档

- 用户文档：[docs/README.md](docs/README.md)
- 安全政策：[SECURITY.md](SECURITY.md)
- 贡献指南：[CONTRIBUTING.md](CONTRIBUTING.md)
- 发布检查清单：[RELEASE.md](RELEASE.md)
- 许可证：[LICENSE](LICENSE)

## License

Apache-2.0. See [LICENSE](LICENSE).

## 鸣谢

demiurge 的设计受到 [OpenClaw](https://github.com/openclaw/openclaw)、[Hermes Agent](https://github.com/NousResearch/hermes-agent) 和 [OpenCode](https://github.com/anomalyco/opencode) 的启发。
