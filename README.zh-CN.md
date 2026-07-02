<p align="center">
  <img src="docs/assets/demiurge-icon-rounded.png" alt="Demiurge icon" width="112">
</p>

<h1 align="center">Demiurge - 德谬歌</h1>

<p align="center">
  <strong>打造文件化、可自进化的 Agent Core。</strong>
</p>

<p align="center">
  <a href="README.md"><kbd>English</kbd></a>
  <kbd><strong>中文</strong></kbd>
</p>

<p align="center">
  <a href="https://allenreder.github.io/demiurge-agent/">网站</a> ·
  <a href="https://allenreder.github.io/demiurge-agent/zh-CN/docs/">文档</a> ·
  <a href="https://allenreder.github.io/demiurge-agent/zh-CN/docs/tutorials/quick-start">快速开始</a> ·
  <a href="https://allenreder.github.io/demiurge-agent/zh-CN/docs/tutorials/customize-agent-core">修改 Core</a> ·
  <a href="https://allenreder.github.io/demiurge-agent/zh-CN/docs/releases/0.4.1">最新发布</a>
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

## 快速开始

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
[快速开始](https://allenreder.github.io/demiurge-agent/zh-CN/docs/tutorials/quick-start)，
然后再配置 provider 或安装 packages。

## Agent Slots 如何工作

Agent Slots 通过安装 package 接入 bootstrap、input 和 output 行为，并由
自定义代码控制 subagents 的调用与逻辑行为，同时让 provider access、
approvals、delivery、promotion 和 rollback 继续由 Host 治理。

<p>
  <strong>Basic Memory System</strong><br>
  <video src="https://github.com/user-attachments/assets/d5c98dae-74e5-452a-9f72-93a8c35b962b" controls muted playsinline width="100%"></video>
</p>

<p>
  <strong>Text-to-speech output</strong><br>
  <video src="https://github.com/user-attachments/assets/cd0af2be-3bb2-4b00-b69c-c0c133d0008e" controls muted playsinline width="100%"></video>
</p>

<p>
  <strong>Speech-to-text input</strong><br>
  <video src="https://github.com/user-attachments/assets/f0cca65a-8586-4599-bb03-583196e58aac" controls muted playsinline width="100%"></video>
</p>

## Core 结构

```text
assistant/
├── agent.yaml
└── agent/
    ├── SOUL.md
    ├── slots.yaml
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

内置 package repository 包含本地记忆、Honcho-backed memory、对话风格提示、
context reseed、provider-owned web search，以及 provider-specific speech
input/output 等可选 packages。

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
