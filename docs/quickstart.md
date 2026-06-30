# Quickstart

This page gets you from a source checkout to a local TUI session.

For a managed checkout install, run:

```bash
scripts/install.sh
```

The default managed checkout is `~/.demiurge/demiurge-agent`; live runtime
agent cores remain under `~/.demiurge/agents`.

The installer first tries `git@github.com:AllenReder/demiurge-agent.git`, then
falls back to `https://github.com/AllenReder/demiurge-agent.git` without an
interactive credential prompt. Use `DEMIURGE_REPO_URL` to install from a local
checkout, fork, or private source.

## 1. Run Tests

```bash
uv run pytest
```

## 2. Initialize Runtime Home

```bash
uv run demiurge init
```

This creates or refreshes the runtime home. The default is `~/.demiurge`:

- `~/.demiurge/config.yaml`
- `~/.demiurge/agents/agent.yaml`
- `~/.demiurge/agents/assistant/`
- `~/.demiurge/agents/evolver/`
- `~/.demiurge/workspace/`

`agents/` in the repository is the source template directory. `~/.demiurge/agents/` contains live runtime cores. A normal `git pull` or package update must not directly overwrite live runtime cores.

Check runtime/template drift without writing files:

```bash
uv run demiurge doctor
uv run demiurge init --check
```

Refresh a specific runtime template:

```bash
uv run demiurge init --refresh assistant
```

Existing runtime templates are backed up to `~/.demiurge/history/` before refresh.

Update a managed checkout without refreshing live runtime cores:

```bash
~/.demiurge/demiurge-agent/.venv/bin/demiurge update
```

## 3. Start the TUI

```bash
uv run demiurge --provider fake
```

The local TUI is a TypeScript/Ink/React frontend connected to the Python host through stdio JSON-RPC. Wheels include the built JS asset. Source development only needs Node.js when editing `ui-tui/`:

```bash
cd ui-tui
npm ci
npm run build
cd ..
```

Tool display defaults to `summary`. Use `quiet` for final messages only:

```bash
uv run demiurge --provider fake --tool-display quiet
```

Use `full` to inspect arguments, results, and model output:

```bash
uv run demiurge --provider fake --tool-display full
```

Common TUI commands:

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

## 4. Use a Real Provider

Store secrets in environment variables:

```bash
export DEMIURGE_MODEL_NAME="gpt-4.1-mini"
export DEMIURGE_BASE_URL="https://api.openai.com/v1"
export DEMIURGE_API_KEY="..."
uv run demiurge --provider openai
```

Temporary CLI overrides also work:

```bash
uv run demiurge --provider openai --model gpt-4.1-mini --api-key "$DEMIURGE_API_KEY"
```

## 5. Choose a Workspace

In the local TUI, file and terminal tools are scoped to the directory where you
launch `uv run demiurge`.

Override it per run:

```bash
uv run demiurge --workspace /path/to/project
```

Or by environment:

```bash
DEMIURGE_WORKSPACE=/path/to/project uv run demiurge
```

External channel runs use the selected core's `agent.yaml` `runtime.workspace`
when no override is set, then fall back to `~/.demiurge/workspace`.
