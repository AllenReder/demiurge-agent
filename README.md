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

Demiurge is an alpha open-source agent framework for running local agents whose
behavior lives in files. The host owns the runtime harness: sessions, turns,
provider calls, tools, approvals, state, delivery, promotion, and rollback. An
Agent Core owns the authored surface: `agent.yaml`, `SOUL.md`, Agent Slots,
skills, tools, schedules, MCP declarations, tests, and local libraries.

Use Demiurge when you want a terminal-first agent runtime where capabilities are
installable and inspectable, while dangerous effects stay behind host-owned
filesystem, terminal, network, state, and versioning boundaries.

Status: **alpha / developer preview**. Runtime layout, authoring contracts, and
package behavior may still change before `1.0.0`. Start with the fake provider
before adding real provider secrets.

## Prerequisites

- `git`
- `uv`
- Node.js 20 or newer for the TUI
- An OpenAI-compatible provider endpoint and API key when you are ready to use a
  real model

## Start with the Fake Provider

The CLI starts the TUI when you run `demiurge` without a subcommand. The main
subcommands are `init`, `doctor`, `package`, `update`, `setup`, and `gateway`.

Managed install is the default user path. The installer requires `git` and `uv`,
creates or reuses the managed checkout at `~/.demiurge/demiurge-agent`, runs
`uv sync`, and initializes the runtime home:

```bash
scripts/install.sh
~/.demiurge/demiurge-agent/.venv/bin/demiurge --provider fake
```

Use a source checkout when you are developing Demiurge itself:

```bash
uv sync --all-groups
uv run demiurge init
uv run demiurge --provider fake
```

The fake provider verifies startup without an API key. Follow the
[Quick Start](https://allenreder.github.io/demiurge-agent/docs/tutorials/quick-start)
for the full first run, then use
[Configure a Provider](https://allenreder.github.io/demiurge-agent/docs/how-to/configure-provider)
to add a real model profile.

## Runtime Shape

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

Workspaces control the filesystem and terminal scope used by tools. Resolution
order is `--workspace`, `DEMIURGE_WORKSPACE`, the TUI launch directory, the
core's `runtime.workspace`, then `~/.demiurge/workspace`.

Provider resolution order is CLI override, core manifest, global fallback, host
default, then `fake`.

## Manual Entry Path

- [Demiurge Manual](https://allenreder.github.io/demiurge-agent/docs/)
- [Quick Start](https://allenreder.github.io/demiurge-agent/docs/tutorials/quick-start)
- [Configure a Provider](https://allenreder.github.io/demiurge-agent/docs/how-to/configure-provider)
- [Choose a Workspace](https://allenreder.github.io/demiurge-agent/docs/how-to/choose-workspace)
- [Troubleshoot](https://allenreder.github.io/demiurge-agent/docs/how-to/troubleshoot)
- [Latest Release: 0.4.1](https://allenreder.github.io/demiurge-agent/docs/releases/0.4.1)

## Contributor Path

For repository workflow and verification rules, see
[CONTRIBUTING.md](CONTRIBUTING.md). For project documentation, start with
[docs/README.md](docs/README.md).

Source checkout development uses `uv sync --all-groups` and `uv run ...`.

## License

Apache-2.0. See [LICENSE](LICENSE).
