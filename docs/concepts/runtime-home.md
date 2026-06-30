# Runtime Home

The runtime home is the live local state root. The default is `~/.demiurge`.

## Layout

```text
~/.demiurge/
  config.yaml
  agents/
    agent.yaml
    assistant/
    evolver/
  registry/
  sessions/
  scheduler/
  logs/
  workspace/
  history/
  demiurge-agent/
```

| Path | Owner | Purpose |
| --- | --- | --- |
| `config.yaml` | Host | Local host preferences such as default core, busy mode, and TUI colors. |
| `agents/agent.yaml` | User/runtime | Global fallback agent config; not a concrete agent core. |
| `agents/<core>/` | User/runtime | Live agent cores. |
| `registry/` | Host | Active version pointers and previous stable versions. |
| `sessions/` | Host | Session metadata, messages, events, artifacts, bootstrap snapshots. |
| `scheduler/<core>/` | Host | Schedule state, run logs, and locks. |
| `logs/mcp-stderr.log` | Host | Stdio MCP server stderr log. |
| `workspace/` | Host/user | Non-local fallback workspace for gateway and scheduler runs. |
| `history/` | Host | Backups from explicit refresh operations. |
| `demiurge-agent/` | Installer | Managed source checkout when installed by `scripts/install.sh`. |

## Source Templates vs Runtime Cores

Repository templates live under source `agents/`. Runtime cores live under
`~/.demiurge/agents/`.

Normal startup fills missing runtime files but does not overwrite edited runtime
cores. Explicit refresh backs up existing runtime files before replacing them
from source templates.

## Overrides

Use a different runtime home:

```bash
uv run demiurge --home ./.demiurge
DEMIURGE_HOME=./.demiurge uv run demiurge
```

Use a different source agent template root:

```bash
uv run demiurge --agents-root /path/to/agents
DEMIURGE_AGENTS_ROOT=/path/to/agents uv run demiurge
```

## Success Check

```bash
uv run demiurge init --check
uv run demiurge --provider fake
```

Then use `/status` and confirm `home`, `agents_root`, `source_agents_root`, and
`session_store`.

## Boundary

Do not edit `sessions/`, `registry/`, or `scheduler/` files by hand while the
runtime is running. Edit agent behavior through runtime cores under
`agents/<core>/` or through `demiurge package`.
