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
  <a href="https://allenreder.github.io/demiurge-agent/docs/">Manual</a> ·
  <a href="docs/tutorials/quick-start.md">Quick Start</a> ·
  <a href="docs/tutorials/customize-agent-core.md">Customize a Core</a> ·
  <a href="docs/reference/contracts/authored-surface.md">Contracts</a> ·
  <a href="docs/releases/0.4.0.md">Latest Release</a>
</p>

Demiurge is an alpha agent framework for building agents whose behavior lives in
files and can evolve under host control. The host owns the runtime harness:
sessions, turns, provider calls, tools, approvals, state, delivery, promotion,
and rollback. An Agent Core owns the authored surface: `agent.yaml`, `SOUL.md`,
Agent Slots, skills, tools, schedules, MCP declarations, tests, and local
libraries.

Use Demiurge when you want a local agent runtime where capabilities are
installable and inspectable, but dangerous effects stay behind a stable host
boundary.

Status: **alpha / developer preview**. Runtime layout, authoring contracts, and
package behavior may still change before `1.0.0`.

## How Agent Slots Work

Agent Slots let packages attach input and output behavior while the host keeps
provider access, approvals, delivery, promotion, and rollback under control.

<p>
  <strong>Speech-to-text input</strong><br>
  <video src="https://github.com/user-attachments/assets/f0cca65a-8586-4599-bb03-583196e58aac" controls muted playsinline width="100%"></video>
</p>

<p>
  <strong>Text-to-speech output</strong><br>
  <video src="https://github.com/user-attachments/assets/cd0af2be-3bb2-4b00-b69c-c0c133d0008e" controls muted playsinline width="100%"></video>
</p>

## Start

Managed install is the default user path:

```bash
scripts/install.sh
~/.demiurge/demiurge-agent/.venv/bin/demiurge --provider fake
```

Source checkout development uses `uv`:

```bash
uv sync --all-groups
uv run demiurge --provider fake
```

The fake provider verifies the runtime without an API key. Use
[`docs/tutorials/quick-start.md`](docs/tutorials/quick-start.md) for the
short quick start, then configure a provider or install packages from there.

## Documentation Map

| Goal | Start here |
| --- | --- |
| Run Demiurge locally | [Quick Start](docs/tutorials/quick-start.md) |
| Modify an Agent Core | [Customize an Agent Core](docs/tutorials/customize-agent-core.md) |
| Build a package repository | [Create an external package repository](docs/tutorials/external-package-repository.md) |
| Configure a real provider | [Configure a provider](docs/how-to/configure-provider.md) |
| Install reusable capabilities | [Install packages](docs/how-to/install-packages.md) |
| Read stable authoring rules | [Authored surface contract](docs/reference/contracts/authored-surface.md) |
| Inspect CLI behavior | [CLI reference](docs/reference/cli.md) |

The hosted manual is available at
[allenreder.github.io/demiurge-agent/docs](https://allenreder.github.io/demiurge-agent/docs/).

## Core Shape

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

The runtime copies source templates into `~/.demiurge/agents`. Edits to runtime
cores are file-backed, diffable, and gateable. Package recipes install reusable
components into those runtime cores without modifying the source templates.

The built-in package repository includes optional packages for local memory,
conversation style hints, context reseed notes, provider-owned web search, and
provider-specific speech input/output.

## Contributor Path

For local development:

```bash
uv sync --all-groups
uv run pytest
```

If you change the TUI:

```bash
cd ui-tui
npm ci
npm test -- --run
npm run typecheck
npm run build
cd ..
cmp ui-tui/dist/entry.js demiurge/ui/tui_dist/entry.js
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for repository workflow and verification
rules.

## License

Apache-2.0. See [LICENSE](LICENSE).
