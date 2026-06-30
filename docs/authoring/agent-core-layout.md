# Agent Core Layout

An agent core is the authored surface under `~/.demiurge/agents/<core>/`.
It is always `agent.yaml + agent/`.

## Minimal Layout

```text
assistant/
  agent.yaml
  agent/
    SOUL.md
    input/
      pipeline.yaml
      base_input/
        slot.yaml
        module.py
    output/
      pipeline.yaml
      base_output/
        slot.yaml
        module.py
```

The repository-level `agents/assistant/` directory is the source template.
Normal startup fills missing runtime files without overwriting local runtime
edits. Explicit `uv run demiurge init --refresh assistant` backs up and
refreshes the runtime copy.

## Authored Surface

| Path | Required | Purpose |
| --- | --- | --- |
| `agent.yaml` | Yes | Core identity, model defaults, slot roots, tools, capabilities, runtime limits, channels. |
| `agent/SOUL.md` | Yes | Core identity and stable behavior instructions. |
| `agent/bootstrap/` | No | Session-start context modules. |
| `agent/input/` | Yes | Modules that add current-turn model input. |
| `agent/output/` | Yes | Modules that deliver model output or structured results. |
| `agent/tools/` | No | Authored tools exposed through the host tool runtime. |
| `agent/skills/` | No | Progressive skill documents loaded with `skill_view`. |
| `agent/schedules/` | No | Cron declarations run by the host scheduler. |
| `agent/mcp/` | No | MCP server declarations for this core. |
| `agent/lib/` | No | Shared authored Python helpers for slots and tools. |
| `agent/tests/` | No | Core-local tests and gate assets. |

## Pipelines

Every core must include:

```text
agent/input/pipeline.yaml
agent/output/pipeline.yaml
```

Pipeline files support `serial` and `parallel` groups:

```yaml
serial:
  - base_input
parallel: []
```

Serial modules run in order and are awaited. Parallel modules run from a
phase-entry snapshot and cannot change the current prompt, current output
result, or current `ctx.result`.

## Source Template vs Runtime Core

```text
repo agents/assistant/      source template
~/.demiurge/agents/assistant/   live runtime core
```

Package installs and hand edits target runtime cores. They do not modify source
templates in the repo.

## Success Check

```bash
uv run demiurge init --check
uv run demiurge --provider fake
```

Use `/status` to confirm the active core and runtime path.

## Boundary

Channels are host adapters, not `agent/` slots. Agent cores may declare channel
configuration in `agent.yaml`, but TUI and Telegram delivery are owned by the
host.
