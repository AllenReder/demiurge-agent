<p align="center">
  <img src="docs/assets/demiurge-icon-rounded.png" alt="Demiurge icon" width="112">
</p>

<h1 align="center">Demiurge</h1>

<p align="center">
  <strong>Build self-evolving agents with independent Agent Cores, modular capabilities, and installable capability packages.</strong>
</p>

<p align="center">
  <kbd><strong>English</strong></kbd>
  <a href="README.zh-CN.md"><kbd>中文</kbd></a>
</p>

<p align="center">
  <a href="https://allenreder.github.io/demiurge-agent/">Website</a> ·
  <a href="https://allenreder.github.io/demiurge-agent/docs/">Docs Site</a> ·
  <a href="docs/README.md">Docs</a> ·
  <a href="docs/getting-started/quickstart.md">Quickstart</a> ·
  <a href="docs/authoring/agent-core-layout.md">Authoring</a> ·
  <a href="docs/operations/channels.md">Channels</a> ·
  <a href="docs/concepts/security-model.md">Security</a>
</p>

Demiurge is a Python agent framework for building self-evolving agents. Independent Agent Cores carry identity and boundaries, while modular design and capability package management make tools, IO, skills, and child cores installable, composable, and iterative.

The host owns sessions, turns, provider calls, tools, approvals, state, delivery, promotion, and rollback, keeping capability evolution inside a clear runtime boundary.

Status: **alpha / developer preview**. APIs, runtime layout, and authoring contracts may still change.

## Why Demiurge?

| Capability | What it means |
| --- | --- |
| Modular IO | Agent cores can shape input, format output, emit local artifacts, and route delivery without taking over host-owned capabilities or approvals. |
| Controlled evolution | Core changes are designed to be file-backed, diffable, testable, and promotable through host-owned version controls. |
| Host-owned harness | Provider calls, tool execution, approvals, state writes, sessions, and delivery stay under a stable runtime boundary. |
| Authored surface | Agent behavior lives in readable files: `SOUL.md`, skills, tools, schedules, IO modules, optional MCP declarations, tests, and optional code slots. |
| Capability packages | Reusable tools, IO modules, skills, libraries, and child cores can be installed into runtime agent cores through package recipes. |
| Local-first runtime | Live cores, sessions, configuration, and non-local fallback workspace live under `~/.demiurge` by default. |

## Quickstart

Managed install is the default path. It creates a runtime home, installs the managed checkout, and starts with the fake provider:

```bash
scripts/install.sh
~/.demiurge/demiurge-agent/.venv/bin/demiurge --provider fake
```

This creates:

- a managed checkout at `~/.demiurge/demiurge-agent`;
- live runtime cores under `~/.demiurge/agents`;
- the non-local fallback tool workspace at `~/.demiurge/workspace`.

Update the managed checkout later:

```bash
~/.demiurge/demiurge-agent/.venv/bin/demiurge update
```

`demiurge update` updates code and dependencies, then runs a read-only runtime drift check. It does not overwrite live agent cores.

## Agent Core and IO

An agent core is the authored surface under `~/.demiurge/agents/<core>/`: `agent.yaml` plus an `agent/` directory.

```text
assistant/
├── agent.yaml
└── agent/
    ├── SOUL.md
    ├── bootstrap/  # optional session-start context
    ├── input/
    ├── output/
    ├── tools/
    ├── skills/
    ├── schedules/
    ├── mcp/
    ├── lib/
    └── tests/
```

The host owns execution, provider calls, tools, approvals, state, sessions, and delivery. The core declares its soul, optional bootstrap context modules, skills, authored tools, channels, schedules, IO modules, optional MCP server tools, and optional code slots.

IO modules are core-local extension points for input shaping and output delivery. They let a core adapt channel input, format responses, emit local artifacts, or route output while still going through host-owned capabilities and approvals.

MCP servers can be declared with `agent/mcp/*.yaml`. The core owns those declarations, while the host owns MCP transports, tool execution, capability checks, approvals, and logging.

See [docs/concepts/host-and-agent-core.md](docs/concepts/host-and-agent-core.md), [docs/authoring/agent-core-layout.md](docs/authoring/agent-core-layout.md), [docs/authoring/input-modules.md](docs/authoring/input-modules.md), and [docs/operations/channels.md](docs/operations/channels.md) for the full authoring model.

## Evolution Boundary

Demiurge treats an agent core as a versionable filesystem surface. The intended evolution path is to propose candidate core changes, evaluate them with tests or runtime checks, then promote or roll them back through the host.

Authored slots should not bypass host-owned controls for dependency changes, dangerous capabilities, production state mutation, provider calls, or tool execution. This keeps agent behavior open to iteration without making the runtime loop itself self-modifying.

## Configure a Real Provider

Demiurge uses an OpenAI-compatible Chat Completions interface:

```bash
export DEMIURGE_MODEL_NAME="gpt-5.4-mini"
export DEMIURGE_BASE_URL="https://api.openai.com/v1"
export DEMIURGE_API_KEY="..."
~/.demiurge/demiurge-agent/.venv/bin/demiurge --provider openai
```

Temporary CLI overrides also work:

```bash
uv run demiurge --provider openai --model deepseek-v4-flash --base-url https://example.com/v1 --api-key "$DEMIURGE_API_KEY"
```

Keep real secrets in environment variables. `/status` shows secret sources, not secret values.

## Telegram Gateway

Enable Telegram in the target core:

```yaml
channels:
  telegram:
    enabled: true
    bot_token_env: DEMIURGE_TELEGRAM_BOT_TOKEN
```

Then run:

```bash
export DEMIURGE_TELEGRAM_BOT_TOKEN="..."
demiurge gateway --core assistant
```

Telegram access is deny-by-default. Private chats require numeric `from.id` in `allowed_users`; groups require both user id and chat id to be allowed.

## Developer Workflow

For source checkout development:

```bash
uv sync --all-groups
uv run pytest
uv run demiurge --provider fake
```

If you change the TUI:

```bash
cd ui-tui
npm ci
npm test -- --run
npm run typecheck
npm run build
cd ..
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full verification workflow.

## Documentation

| Page | Purpose |
| --- | --- |
| [Project website](https://allenreder.github.io/demiurge-agent/) | Public project homepage and hosted documentation site. |
| [Hosted docs](https://allenreder.github.io/demiurge-agent/docs/) | GitHub Pages version of the manual. |
| [docs/README.md](docs/README.md) | User documentation index. |
| [docs/getting-started/quickstart.md](docs/getting-started/quickstart.md) | Install, initialize runtime home, and start the TUI. |
| [docs/concepts/host-and-agent-core.md](docs/concepts/host-and-agent-core.md) | Host-owned harness and agent-core authored-surface boundary. |
| [docs/authoring/agent-core-layout.md](docs/authoring/agent-core-layout.md) | Agent core layout and authored module roots. |
| [docs/operations/channels.md](docs/operations/channels.md) | Local TUI and Telegram gateway behavior. |
| [docs/concepts/security-model.md](docs/concepts/security-model.md) | Workspace scope, approvals, and channel trust boundaries. |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Development and verification workflow. |
| [RELEASE.md](RELEASE.md) | Release checklist. |

## License

Apache-2.0. See [LICENSE](LICENSE).

## Acknowledgements

Demiurge's design has been informed by [OpenClaw](https://github.com/openclaw/openclaw), [Hermes Agent](https://github.com/NousResearch/hermes-agent), [Eve](https://github.com/vercel/eve), and [OpenCode](https://github.com/anomalyco/opencode).
