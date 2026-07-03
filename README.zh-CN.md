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
  <a href="https://allenreder.github.io/demiurge-agent/zh-CN/docs/releases/0.6.0">最新发布</a>
</p>

Demiurge 是一个 Alpha 阶段的智能体框架，使用独特的 **Agent Slots** 拓展能力边界与逻辑设计而不影响 Harness。并可以在 Host 的受控环境中自我迭代。具有文件化设计的 Agent Core 可以在 Host 的控制下实现多 Agent 协作、状态管理、工具组合、技能组合、MCP 组合和自我进化。

状态：**alpha / developer preview**。在 `1.0.0` 之前，runtime layout、
authored-surface contracts 和 package behavior 都仍可能变化。

## 前置条件

- `git`
- `uv`
- TUI 需要 Node.js 20 或更新版本
- 准备使用真实模型时，需要 OpenAI-compatible provider endpoint 和 API key

## 快速开始

默认用户路径是 managed install：

```bash
scripts/install.sh
~/.demiurge/demiurge-agent/.venv/bin/demiurge init
~/.demiurge/demiurge-agent/.venv/bin/demiurge --provider fake
```

源码 checkout 开发使用 `uv`：

```bash
uv sync --all-groups
uv run demiurge --provider fake
```

如果你想使用真实 provider，运行 `demiurge setup` 配置 API key 和 endpoint。

完整首次运行见
[快速开始](https://allenreder.github.io/demiurge-agent/zh-CN/docs/tutorials/quick-start)，
然后用
[配置 Provider](https://allenreder.github.io/demiurge-agent/zh-CN/docs/how-to/configure-provider)
添加真实模型 profile。

## Agent Slots 如何工作

Agent Slot 是 Agent Core 的可演化交互边界：它让 Core 定义的行为逻辑在受治理的位置介入 agent loop，并组合 tools、skills、MCP、state 或其他 agents，而不需要修改 Host harness。Host 仍然控制 provider access、approvals、delivery、Git revision promotion 和 rollback。

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

## Agent Core 结构

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
    └── lib/
```

runtime 会从 source `agents/` tree 初始化 `~/.demiurge/.core.git`，并把
live agents tree checkout 到 `~/.demiurge/agents`。runtime core edits、
evolve runs、package install/uninstall、promotion 和 rollback 都是这个 Git
repository 里的 revisions。直接修改 `~/.demiurge/agents` 会在 run/edit workflows
加载 live core 前保存为 core revision。Package recipes 会把可复用组件安装进 runtime
cores，不修改 source templates 或 host lock file。

## 手册入口

- [Demiurge Manual](https://allenreder.github.io/demiurge-agent/zh-CN/docs/)
- [快速开始](https://allenreder.github.io/demiurge-agent/zh-CN/docs/tutorials/quick-start)
- [配置 Provider](https://allenreder.github.io/demiurge-agent/zh-CN/docs/how-to/configure-provider)
- [选择 Workspace](https://allenreder.github.io/demiurge-agent/zh-CN/docs/how-to/choose-workspace)
- [故障排查](https://allenreder.github.io/demiurge-agent/zh-CN/docs/how-to/troubleshoot)
- [编写 Package Recipe](https://allenreder.github.io/demiurge-agent/zh-CN/docs/how-to/write-package-recipe)
- [发布 Package Repository](https://allenreder.github.io/demiurge-agent/zh-CN/docs/how-to/publish-package-repository)
- [最新发布：0.6.0](https://allenreder.github.io/demiurge-agent/zh-CN/docs/releases/0.6.0)

## 开发者路径

仓库工作流和验证规则见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## License

Apache-2.0. See [LICENSE](LICENSE).
