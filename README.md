# demiurge

`demiurge` is a local-first Python agent harness where the host owns the runtime loop, tools, approvals, state, delivery, and versioning, while each agent core stays as an authored `agent.yaml + agent/` surface.

Status: **alpha / developer preview**. APIs, runtime layout, and authoring contracts may still change.

中文说明: [README.zh-CN.md](README.zh-CN.md)

## Quickstart

Managed install is the default path:

```bash
scripts/install.sh
~/.demiurge/demiurge-agent/.venv/bin/demiurge --provider fake
```

This creates:

- a managed checkout at `~/.demiurge/demiurge-agent`;
- live runtime cores under `~/.demiurge/agents`;
- the default tool workspace at `~/.demiurge/workspace`.

Update the managed checkout later:

```bash
~/.demiurge/demiurge-agent/.venv/bin/demiurge update
```

`demiurge update` updates code and dependencies, then runs a read-only runtime drift check. It does not overwrite live agent cores.

## Agent Cores and IO

An agent core is the authored surface under `~/.demiurge/agents/<core>/`: `agent.yaml` plus an `agent/` directory. The host owns execution, provider calls, tools, approvals, state, sessions, and delivery; the core declares instructions, skills, channels, and optional code slots.

IO modules are core-local extension points for input shaping and output delivery. They let a core adapt channel input, format responses, emit local artifacts, or route output while still going through host-owned capabilities and approvals.

See [docs/agents.md](docs/agents.md), [docs/agent-core-authoring.md](docs/agent-core-authoring.md), and [docs/channels.md](docs/channels.md) for the full authoring model.

## Configure a Real Provider

demiurge uses an OpenAI-compatible Chat Completions interface:

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

- User documentation: [docs/README.md](docs/README.md)
- Security policy: [SECURITY.md](SECURITY.md)
- Contribution guide: [CONTRIBUTING.md](CONTRIBUTING.md)
- Release checklist: [RELEASE.md](RELEASE.md)
- License: [LICENSE](LICENSE)

## License

Apache-2.0. See [LICENSE](LICENSE).

## Acknowledgements

demiurge's design has been informed by [OpenClaw](https://github.com/openclaw/openclaw), [Hermes Agent](https://github.com/NousResearch/hermes-agent), and [OpenCode](https://github.com/anomalyco/opencode).
