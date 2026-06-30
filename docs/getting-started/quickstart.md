# Quickstart

This guide gets you from a checkout to a working local TUI session. Use the
fake provider first; it verifies the host, runtime home, source templates, TUI,
and session storage without requiring an API key.

## 1. Install or Sync

Managed install is the default user path:

```bash
scripts/install.sh
```

The managed checkout lives at `~/.demiurge/demiurge-agent`. Live runtime cores
live separately under `~/.demiurge/agents`, so updates do not overwrite edited
agent cores.

For source checkout development:

```bash
uv sync --all-groups
```

Success check:

```bash
uv run demiurge --help
```

## 2. Initialize Runtime Home

```bash
uv run demiurge init
```

This creates or refreshes:

```text
~/.demiurge/
  config.yaml
  agents/
    agent.yaml
    assistant/
    evolver/
  workspace/
```

Check drift without writing files:

```bash
uv run demiurge init --check
uv run demiurge doctor
```

If this fails, read [../operations/troubleshooting.md](../operations/troubleshooting.md).

## 3. Start the Local TUI

```bash
uv run demiurge --provider fake
```

The TUI is the default local interface. It connects to the Python host over
stdio JSON-RPC. Source development only needs Node.js when editing `ui-tui/`;
wheels include the built JavaScript asset.

Useful TUI commands:

- `/help`
- `/status`
- `/tools`
- `/sessions`
- `/resume`
- `/events`
- `/tool-display quiet|summary|full`
- `/busy interrupt|queue`
- `/interrupt`
- `/versions`
- `/evolve <goal>`
- `/rollback`
- `/exit`

Success check: `/status` should show the selected core, runtime home, workspace,
provider, model source, and session path.

## 4. Use a Real Provider

Demiurge stores provider connection details in host config. The interactive
setup command can create a provider profile, write secrets to
`~/.demiurge/.env`, and set the active core model:

```bash
uv run demiurge setup
```

Scripted setup is also available:

```bash
uv run demiurge setup providers add openai --preset openai --set-default
uv run demiurge setup model set --core assistant --provider openai --model gpt-4.1-mini
uv run demiurge --provider openai
```

`/status` shows provider, model, endpoint, and API key sources without printing
secret values. See [configure-provider.md](configure-provider.md).

## 5. Choose a Workspace

File and terminal tools are scoped to the resolved workspace.

Local TUI default:

```bash
cd /path/to/project
uv run demiurge --provider fake
```

Override per run:

```bash
uv run demiurge --workspace /path/to/project
DEMIURGE_WORKSPACE=/path/to/project uv run demiurge
```

Gateway, Telegram, and scheduler runs use the selected core's
`agent.yaml` `runtime.workspace` when no override is set, then fall back to
`~/.demiurge/workspace`.

## Boundary

This quickstart does not customize an agent core. Before editing runtime cores,
read [../concepts/host-and-agent-core.md](../concepts/host-and-agent-core.md)
and [../authoring/agent-core-layout.md](../authoring/agent-core-layout.md).
