<p align="center">
  <img src="docs/assets/demiurge-icon-rounded.png" alt="Demiurge icon" width="112">
</p>

<h1 align="center">Demiurge</h1>

<p align="center">
  <strong>Build file-backed, self-evolving Agent Cores.</strong>
</p>

<p align="center">
  <kbd><strong>English</strong></kbd>
  <a href="README.zh-CN.md"><kbd>中文</kbd></a>
</p>

<p align="center">
  <a href="https://allenreder.github.io/demiurge-agent/">Website</a> ·
  <a href="https://allenreder.github.io/demiurge-agent/docs/">Docs</a> ·
  <a href="https://allenreder.github.io/demiurge-agent/docs/tutorials/quick-start">Quick Start</a> ·
  <a href="https://allenreder.github.io/demiurge-agent/docs/tutorials/customize-agent-core">Customize a Core</a> ·
  <a href="https://allenreder.github.io/demiurge-agent/docs/releases/0.4.1">Latest Release</a>
</p>

Demiurge is an Alpha-stage agent framework that uses the distinctive
**Agent Slots** model to extend capability boundaries and logic design without
changing the Harness. It can also self-iterate inside a Host-controlled
environment. A file-backed Agent Core can implement multi-agent collaboration,
state management, tool composition, skill composition, MCP composition, and
self-evolution under Host control.

Status: **alpha / developer preview**. Runtime layout, authoring contracts, and
package behavior may still change before `1.0.0`.

## Prerequisites

- `git`
- `uv`
- Node.js 20 or newer for the TUI
- An OpenAI-compatible provider endpoint and API key when you are ready to use a
  real model

## Quick Start

Managed install is the default user path:

```bash
scripts/install.sh
~/.demiurge/demiurge-agent/.venv/bin/demiurge init
~/.demiurge/demiurge-agent/.venv/bin/demiurge --provider fake
```

Source checkout development uses `uv`:

```bash
uv sync --all-groups
uv run demiurge --provider fake
```

If you want to use a real provider, run `demiurge setup` to configure your API key and endpoint.

Follow the
[Quick Start](https://allenreder.github.io/demiurge-agent/docs/tutorials/quick-start)
for the full first run, then use
[Configure a Provider](https://allenreder.github.io/demiurge-agent/docs/how-to/configure-provider)
to add a real model profile.

## How Agent Slots Work

Agent Slots let packages attach bootstrap, input, and output behavior, and let
custom code control subagent calls and authored logic, while the host keeps
provider access, approvals, delivery, promotion, and rollback under control.

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


## Agent Core Shape

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

The runtime copies source templates into `~/.demiurge/agents`. Edits to runtime
cores are file-backed, diffable, and gateable. Package recipes install reusable
components into those runtime cores without modifying the source templates.


## Manual Entry Path

- [Demiurge Manual](https://allenreder.github.io/demiurge-agent/docs/)
- [Quick Start](https://allenreder.github.io/demiurge-agent/docs/tutorials/quick-start)
- [Configure a Provider](https://allenreder.github.io/demiurge-agent/docs/how-to/configure-provider)
- [Choose a Workspace](https://allenreder.github.io/demiurge-agent/docs/how-to/choose-workspace)
- [Troubleshoot](https://allenreder.github.io/demiurge-agent/docs/how-to/troubleshoot)
- [Latest Release: 0.4.1](https://allenreder.github.io/demiurge-agent/docs/releases/0.4.1)

## Contributor Path

For repository workflow and verification rules, see
[CONTRIBUTING.md](CONTRIBUTING.md).

## License

Apache-2.0. See [LICENSE](LICENSE).
