# demiurge Documentation

These pages describe the user-facing behavior of demiurge. The root README also has a Chinese mirror: [../README.zh-CN.md](../README.zh-CN.md).

## Start Here

| Page | Purpose |
| --- | --- |
| [quickstart.md](quickstart.md) | Install from a checkout, initialize runtime home, and start the TUI. |
| [configuration.md](configuration.md) | Runtime home, workspace, fallback config, model env vars, and host preferences. |
| [agents.md](agents.md) | Agent core locations, structure, initialization, and refresh behavior. |
| [agent-core-authoring.md](agent-core-authoring.md) | Author input/output modules and customize a runtime assistant core. |
| [security.md](security.md) | Workspace scope, sensitive paths, approval policy, and channel trust boundaries. |

## Capabilities

| Page | Purpose |
| --- | --- |
| [providers.md](providers.md) | Fake and OpenAI-compatible provider configuration. |
| [tools.md](tools.md) | Built-in tools, approval, workspace access, and output shaping. |
| [skills.md](skills.md) | `agent/skills/` format and progressive skill loading. |
| [packages.md](packages.md) | Built-in agent catalog, package wizard, presets, and installed package records. |
| [schedules.md](schedules.md) | `agent/schedules/` cron declarations, run semantics, and delivery. |
| [channels.md](channels.md) | Local TUI and Telegram polling gateway behavior. |
| [sessions.md](sessions.md) | Durable sessions, resume, context assembly, and manual compaction. |

## Reference

| Page | Purpose |
| --- | --- |
| [runtime-home.md](runtime-home.md) | Runtime directory layout under `~/.demiurge`. |
| [cli.md](cli.md) | Command-line flags and subcommands. |
| [troubleshooting.md](troubleshooting.md) | Common failure modes and recovery steps. |

## Short Path

```bash
uv run demiurge init
uv run demiurge --provider fake
```

For a real model, use an OpenAI-compatible endpoint and keep secrets in environment variables:

```bash
export DEMIURGE_MODEL_NAME="gpt-4.1-mini"
export DEMIURGE_API_KEY="..."
uv run demiurge --provider openai
```

The default local entry is the TUI. External channels are started with:

```bash
uv run demiurge gateway --core assistant
```

External channels only listen when enabled in the current core. v1 supports Telegram.
