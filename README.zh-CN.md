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

Demiurge 是一个 alpha 阶段的开源 agent framework，用来运行行为保存在文件
中的本地 Agent。Host 负责 runtime harness：session、turn、provider 调用、
工具、审批、状态、delivery、promotion 和 rollback。Agent Core 负责 authored
surface：`agent.yaml`、`SOUL.md`、Agent Slots、skills、tools、schedules、
MCP declarations、tests 和本地库。

如果你希望 agent 能在本地终端运行，能力可以安装、组合、检查，同时文件系统、
终端、网络、状态和版本切换等危险效果都经过 Host 边界，Demiurge 就是为这个
方向设计的。

状态：**alpha / developer preview**。在 `1.0.0` 之前，runtime layout、
authored-surface contracts 和 package behavior 都仍可能变化。第一次运行请先
用 fake provider，确认 runtime 正常后再加入真实 provider secrets。

## 前置条件

- `git`
- `uv`
- TUI 需要 Node.js 20 或更新版本
- 准备使用真实模型时，需要 OpenAI-compatible provider endpoint 和 API key

## 先用 Fake Provider 启动

没有 subcommand 时，`demiurge` 会启动 TUI。主要 subcommands 是 `init`、
`doctor`、`package`、`update`、`setup` 和 `gateway`。

默认用户路径是 managed install。安装脚本需要 `git` 和 `uv`，会创建或复用
`~/.demiurge/demiurge-agent` managed checkout，运行 `uv sync`，并初始化
runtime home：

```bash
scripts/install.sh
~/.demiurge/demiurge-agent/.venv/bin/demiurge --provider fake
```

如果你要开发 Demiurge 本身，使用 source checkout：

```bash
uv sync --all-groups
uv run demiurge init
uv run demiurge --provider fake
```

fake provider 不需要 API key，可用于验证启动路径。完整首次运行见
[快速开始](https://allenreder.github.io/demiurge-agent/zh-CN/docs/tutorials/quick-start)，
然后用
[配置 Provider](https://allenreder.github.io/demiurge-agent/zh-CN/docs/how-to/configure-provider)
添加真实模型 profile。

## Runtime 结构

```text
assistant/
├── agent.yaml
└── agent/
    ├── SOUL.md
    ├── pipelines.yaml
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

workspace 决定文件和终端工具的作用范围。解析顺序是 `--workspace`、
`DEMIURGE_WORKSPACE`、TUI 启动目录、core 的 `runtime.workspace`，最后是
`~/.demiurge/workspace`。

provider 解析顺序是 CLI override、core manifest、global fallback、host
default，最后是 `fake`。

## 手册入口

- [Demiurge Manual](https://allenreder.github.io/demiurge-agent/zh-CN/docs/)
- [快速开始](https://allenreder.github.io/demiurge-agent/zh-CN/docs/tutorials/quick-start)
- [配置 Provider](https://allenreder.github.io/demiurge-agent/zh-CN/docs/how-to/configure-provider)
- [选择 Workspace](https://allenreder.github.io/demiurge-agent/zh-CN/docs/how-to/choose-workspace)
- [故障排查](https://allenreder.github.io/demiurge-agent/zh-CN/docs/how-to/troubleshoot)
- [最新发布：0.4.1](https://allenreder.github.io/demiurge-agent/zh-CN/docs/releases/0.4.1)

## 开发者路径

仓库工作流和验证规则见 [CONTRIBUTING.md](CONTRIBUTING.md)。项目文档从
[docs/README.md](docs/README.md) 开始。Source checkout 开发使用
`uv sync --all-groups` 和 `uv run ...`。

## License

Apache-2.0. See [LICENSE](LICENSE).
