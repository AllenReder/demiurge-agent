<p align="center">
  <img src="docs/assets/demiurge-icon-rounded.png" alt="Demiurge icon" width="112">
</p>

<h1 align="center">Demiurge</h1>

<p align="center">
  <strong>自由打造会自我进化的 Agent。</strong>
</p>

<p align="center">
  <a href="README.md"><kbd>English</kbd></a>
  <kbd><strong>中文</strong></kbd>
</p>

<p align="center">
  <a href="https://allenreder.github.io/demiurge-agent/">网站</a> ·
  <a href="https://allenreder.github.io/demiurge-agent/docs/">文档站</a> ·
  <a href="docs/README.md">文档</a> ·
  <a href="docs/getting-started/quickstart.md">快速开始</a> ·
  <a href="docs/authoring/agent-core-layout.md">Core 编写</a> ·
  <a href="docs/operations/channels.md">Channels</a> ·
  <a href="docs/concepts/security-model.md">安全模型</a>
</p>

Demiurge 是一个用于打造会自我进化的 Agent 的 Python agent framework。独立 Agent Core 承载个性与边界，模块化设计和能力包管理让工具、IO、技能与子 Core 可安装、可组合、可迭代。

host 负责 session、turn、provider 调用、工具、审批、状态、delivery、promotion 和 rollback，让能力进化始终发生在清晰的 runtime 边界内。

状态：**alpha / developer preview**。API、runtime 布局和 authoring contract 仍可能变化。

## 为什么是 Demiurge？

| 能力 | 含义 |
| --- | --- |
| 可拓展 IO | Agent core 可以整理输入、格式化输出、生成本地 artifact、路由 delivery，而不接管 host 拥有的 capabilities 和 approvals。 |
| 受控进化 | Core 变更以文件为边界，天然可 diff、可测试，并通过 host 拥有的版本控制进行 promote 或 rollback。 |
| Host-owned harness | Provider 调用、工具执行、审批、状态写入、session 和 delivery 始终处在稳定 runtime 边界内。 |
| Authored surface | Agent 行为存在于可读文件中：`SOUL.md`、skills、tools、schedules、IO modules、可选 MCP declarations、tests 和可选 code slots。 |
| 能力包管理 | 可复用的 tools、IO modules、skills、libraries 和子 Core 可以通过 package recipes 安装进 runtime agent core。 |
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
    ├── SOUL.md
    ├── bootstrap/  # 可选 session-start context
    ├── input/
    ├── output/
    ├── tools/
    ├── skills/
    ├── schedules/
    ├── mcp/
    ├── lib/
    └── tests/
```

host 负责执行、provider 调用、工具、审批、状态、session 和 delivery。core 声明 soul、可选 bootstrap context modules、skills、authored tools、channels、schedules、IO modules、可选 MCP server tools 和可选 code slots。

IO modules 是 core-local 的 input shaping 和 output delivery 扩展点。它们让 core 能适配 channel input、格式化回复、产生本地 artifact，或路由 output，同时仍经过宿主负责的 capabilities 和 approvals。

MCP servers 可以通过 `agent/mcp/*.yaml` 声明。core 拥有这些声明；MCP transports、tool execution、capability checks、approvals 和 logging 仍由 host 拥有。

完整 authoring model 见 [docs/concepts/host-and-agent-core.md](docs/concepts/host-and-agent-core.md)、[docs/authoring/agent-core-layout.md](docs/authoring/agent-core-layout.md)、[docs/authoring/input-modules.md](docs/authoring/input-modules.md) 和 [docs/operations/channels.md](docs/operations/channels.md)。

## 进化边界

Demiurge 把 agent core 当作可版本化的文件系统 surface。预期的进化路径是：先提出候选 core 变更，用测试或 runtime check 评估，再由 host 负责 promote 或 rollback。

authored slots 不应绕过 host 对 dependency change、危险 capability、production state mutation、provider 调用或工具执行的控制。这样 agent 行为可以持续迭代，但 runtime loop 本身不会变成随意自修改的对象。

## 配置真实 Provider

Demiurge 使用 OpenAI-compatible Chat Completions 接口：

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
| [项目网站](https://allenreder.github.io/demiurge-agent/) | 公开项目首页和托管文档站入口。 |
| [托管文档](https://allenreder.github.io/demiurge-agent/docs/) | GitHub Pages 上的手册版本。 |
| [docs/README.md](docs/README.md) | 用户文档入口。 |
| [docs/getting-started/quickstart.md](docs/getting-started/quickstart.md) | 安装、初始化 runtime home 和启动 TUI。 |
| [docs/concepts/host-and-agent-core.md](docs/concepts/host-and-agent-core.md) | host-owned harness 和 agent-core authored surface 边界。 |
| [docs/authoring/agent-core-layout.md](docs/authoring/agent-core-layout.md) | agent core 目录结构和 authored module roots。 |
| [docs/operations/channels.md](docs/operations/channels.md) | 本地 TUI 和 Telegram gateway 行为。 |
| [docs/concepts/security-model.md](docs/concepts/security-model.md) | workspace scope、审批和 channel trust boundary。 |
| [CONTRIBUTING.md](CONTRIBUTING.md) | 开发与验证流程。 |
| [RELEASE.md](RELEASE.md) | 发布检查清单。 |

## License

Apache-2.0. See [LICENSE](LICENSE).

## 鸣谢

Demiurge 的设计受到 [OpenClaw](https://github.com/openclaw/openclaw)、[Hermes Agent](https://github.com/NousResearch/hermes-agent)、[Eve](https://github.com/vercel/eve) 和 [OpenCode](https://github.com/anomalyco/opencode) 的启发。
