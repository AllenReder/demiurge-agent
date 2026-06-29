<!-- Logo/wordmark slot: add docs/assets/demiurge-wordmark.svg here when the asset is ready. -->

<h1 align="center">demiurge</h1>

<p align="center">
  <strong>一个本地优先、IO 可拓展、面向 Agent Core 受控进化的 Python agent framework。</strong>
</p>

<p align="center">
  <a href="README.md"><kbd>English</kbd></a>
  <kbd><strong>中文</strong></kbd>
</p>

<p align="center">
  <a href="docs/README.md">文档</a> ·
  <a href="docs/quickstart.md">快速开始</a> ·
  <a href="docs/agent-core-authoring.md">Core 编写</a> ·
  <a href="docs/channels.md">Channels</a> ·
  <a href="docs/security.md">安全模型</a>
</p>

`demiurge` 是一个 local-first 的 Python agent harness。host 负责 session、turn、provider 调用、工具、审批、状态、delivery、promotion 和 rollback；每个 agent core 则保持为可检查、可修改的 `agent.yaml + agent/` authored surface。

这个边界让 runtime 保持稳定，同时让 IO modules、skills、schedules 和候选 core 变更能在清晰边界内持续演进。

状态：**alpha / developer preview**。API、runtime 布局和 authoring contract 仍可能变化。

## 为什么是 demiurge？

| 能力 | 含义 |
| --- | --- |
| 可拓展 IO | Agent core 可以整理输入、格式化输出、生成本地 artifact、路由 delivery，而不接管 host 拥有的 capabilities 和 approvals。 |
| 受控进化 | Core 变更以文件为边界，天然可 diff、可测试，并通过 host 拥有的版本控制进行 promote 或 rollback。 |
| Host-owned harness | Provider 调用、工具执行、审批、状态写入、session 和 delivery 始终处在稳定 runtime 边界内。 |
| Authored surface | Agent 行为存在于可读文件中：instructions、skills、schedules、IO modules、tests 和可选 code slots。 |
| Local-first runtime | live cores、sessions、配置和 workspace 默认放在本机 `~/.demiurge` 下。 |

## 快速开始

默认推荐 managed install。它会创建 runtime home、安装 managed checkout，并用 fake provider 启动：

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

## Agent Core 和 IO

agent core 是 `~/.demiurge/agents/<core>/` 下的 authored surface：`agent.yaml` 加一个 `agent/` 目录。

```text
assistant/
├── agent.yaml
└── agent/
    ├── instructions.md
    ├── skills/
    ├── schedules/
    ├── input/
    ├── output/
    ├── lib/
    └── tests/
```

host 负责执行、provider 调用、工具、审批、状态、session 和 delivery。core 声明 instructions、skills、channels、schedules、IO modules 和可选 code slots。

IO modules 是 core-local 的 input shaping 和 output delivery 扩展点。它们让 core 能适配 channel input、格式化回复、产生本地 artifact，或路由 output，同时仍经过宿主负责的 capabilities 和 approvals。

完整 authoring model 见 [docs/agents.md](docs/agents.md)、[docs/agent-core-authoring.md](docs/agent-core-authoring.md) 和 [docs/channels.md](docs/channels.md)。

## 进化边界

demiurge 把 agent core 当作可版本化的文件系统 surface。预期的进化路径是：先提出候选 core 变更，用测试或 runtime check 评估，再由 host 负责 promote 或 rollback。

authored slots 不应绕过 host 对 dependency change、危险 capability、production state mutation、provider 调用或工具执行的控制。这样 agent 行为可以持续迭代，但 runtime loop 本身不会变成随意自修改的对象。

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

| 页面 | 内容 |
| --- | --- |
| [docs/README.md](docs/README.md) | 用户文档入口。 |
| [docs/quickstart.md](docs/quickstart.md) | 安装、初始化 runtime home 和启动 TUI。 |
| [docs/agent-core-authoring.md](docs/agent-core-authoring.md) | 编写 IO modules 并定制 runtime agent cores。 |
| [docs/channels.md](docs/channels.md) | 本地 TUI 和 Telegram gateway 行为。 |
| [docs/security.md](docs/security.md) | workspace scope、审批和 channel trust boundary。 |
| [CONTRIBUTING.md](CONTRIBUTING.md) | 开发与验证流程。 |
| [RELEASE.md](RELEASE.md) | 发布检查清单。 |

## License

Apache-2.0. See [LICENSE](LICENSE).

## 鸣谢

demiurge 的设计受到 [OpenClaw](https://github.com/openclaw/openclaw)、[Hermes Agent](https://github.com/NousResearch/hermes-agent)、[Eve](https://github.com/vercel/eve) 和 [OpenCode](https://github.com/anomalyco/opencode) 的启发。
