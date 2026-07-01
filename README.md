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
  <a href="https://allenreder.github.io/demiurge-agent/docs/releases/0.4.0">Latest Release</a>
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

## Quick Start

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
[Quick Start](https://allenreder.github.io/demiurge-agent/docs/tutorials/quick-start)
for the short tutorial, then configure a provider or install packages from there.

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
