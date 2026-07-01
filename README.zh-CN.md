<p align="center">
  <img src="docs/assets/demiurge-icon-rounded.png" alt="Demiurge icon" width="112">
</p>

<h1 align="center">Demiurge - 德谬歌</h1>

<p align="center">
  <strong>用于文件化、自进化 Agent Core 的 local-first Python framework。</strong>
</p>

<p align="center">
  <a href="README.md"><kbd>English</kbd></a>
  <kbd><strong>中文</strong></kbd>
</p>

<p align="center">
  <a href="https://allenreder.github.io/demiurge-agent/">网站</a> ·
  <a href="https://allenreder.github.io/demiurge-agent/docs/">手册</a> ·
  <a href="docs/tutorials/quick-start.md">快速开始</a> ·
  <a href="docs/tutorials/customize-agent-core.md">修改 Core</a> ·
  <a href="docs/reference/contracts/authored-surface.md">契约</a> ·
  <a href="docs/releases/0.4.0.md">最新发布</a>
</p>

Demiurge 是一个 alpha 阶段的 agent framework。agent 行为保存在文件中，
并通过 host 控制的版本、审批和 gate 流程进行演进。host 负责 runtime
harness：session、turn、provider 调用、工具、审批、状态、delivery、
promotion 和 rollback。Agent Core 负责 authored surface：`agent.yaml`、
`SOUL.md`、slot modules、skills、tools、schedules、MCP declarations、tests
和本地库。

如果你希望 agent 能安装、组合、检查能力，同时又不让危险效果绕过 runtime
边界，Demiurge 就是为这个方向设计的。

状态：**alpha / developer preview**。在 `1.0.0` 之前，runtime layout、
authored-surface contracts 和 package behavior 都仍可能变化。

## 开始

默认用户路径是 managed install：

```bash
scripts/install.sh
~/.demiurge/demiurge-agent/.venv/bin/demiurge --provider fake
```

源码 checkout 开发使用 `uv`：

```bash
uv sync --all-groups
uv run demiurge --provider fake
```

fake provider 不需要 API key，适合先验证 runtime。短启动路径见
[快速开始](docs/tutorials/quick-start.md)，然后再配置 provider 或安装 packages。

## 文档入口

| 目标 | 入口 |
| --- | --- |
| 本地运行 Demiurge | [快速开始](docs/tutorials/quick-start.md) |
| 修改 Agent Core | [修改 Agent Core](docs/tutorials/customize-agent-core.md) |
| 创建外部 package repository | [创建外部 package repository](docs/tutorials/external-package-repository.md) |
| 配置真实 provider | [配置 provider](docs/how-to/configure-provider.md) |
| 安装可复用能力 | [安装 packages](docs/how-to/install-packages.md) |
| 阅读稳定 authored-surface 规则 | [Authored surface contract](docs/reference/contracts/authored-surface.md) |
| 查看 CLI 行为 | [CLI reference](docs/reference/cli.md) |

托管手册位于
[allenreder.github.io/demiurge-agent/docs](https://allenreder.github.io/demiurge-agent/docs/)。

## Core 结构

```text
assistant/
├── agent.yaml
└── agent/
    ├── SOUL.md
    ├── bootstrap/
    ├── input/
    ├── output/
    ├── tools/
    ├── skills/
    ├── schedules/
    ├── mcp/
    ├── lib/
    └── tests/
```

runtime 会把 source templates 复制到 `~/.demiurge/agents`。runtime core
的改动是文件化、可 diff、可 gate 的。Package recipes 会把可复用组件安装进
runtime cores，不修改 source templates。

内置 package repository 包含本地记忆、对话风格提示、context reseed、provider-owned
web search，以及 provider-specific speech input/output 等可选 packages。

## 开发者路径

```bash
uv sync --all-groups
uv run pytest
```

如果修改 TUI：

```bash
cd ui-tui
npm ci
npm test -- --run
npm run typecheck
npm run build
cd ..
cmp ui-tui/dist/entry.js demiurge/ui/tui_dist/entry.js
```

仓库工作流和验证规则见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## License

Apache-2.0. See [LICENSE](LICENSE).
