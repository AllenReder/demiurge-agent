# Configuration

Demiurge separates host configuration, global fallback agent configuration, and
concrete core configuration.

## Host Runtime Config

`<home>/config.yaml` stores host-level preferences. Default path:

```text
~/.demiurge/config.yaml
```

Example:

```yaml
runtime:
  default_core: assistant
channel:
  busy_mode: interrupt
ui:
  user_message_align: left
  demiurge_theme_color: ff9afc
  user_theme_color: 9cc9ff
debug:
  show_system_prompt: false
providers:
  default: null
  profiles: {}
```

This file is not an agent core and not the global fallback agent config.

`debug.show_system_prompt` is a local troubleshooting switch. When enabled, the
host sends each assembled system prompt to the active channel immediately before
the provider call. The delivery is transient and is not written to
`messages.jsonl`, but it can expose sensitive instructions, memory, bootstrap
context, or other system context to the channel.

## Provider Profiles

Provider connection details are host-owned:

```yaml
providers:
  default: deepseek
  profiles:
    deepseek:
      adapter: openai-compatible
      base_url: https://api.deepseek.com
      api_key_env: DEEPSEEK_API_KEY
      api_key: null
```

`api_key_env` reads from `~/.demiurge/.env` first, then the shell environment.
If both `api_key_env` and direct `api_key` are present, the environment value
wins. Use `demiurge setup` to create and inspect these profiles.

## Runtime Home

```bash
uv run demiurge --home ./.demiurge
DEMIURGE_HOME=./.demiurge uv run demiurge
```

See [../concepts/runtime-home.md](../concepts/runtime-home.md).

## Workspace

File and terminal tools can only access the resolved workspace.

```bash
uv run demiurge --workspace /path/to/project
DEMIURGE_WORKSPACE=/path/to/project uv run demiurge
```

Concrete core default for non-local runs:

```yaml
runtime:
  workspace: /path/to/project
```

Do not put `runtime.workspace` in `<home>/config.yaml`; it belongs to a
concrete core or process override.

## Source Agents Root

```bash
uv run demiurge --agents-root /path/to/agents
DEMIURGE_AGENTS_ROOT=/path/to/agents uv run demiurge
```

Source templates are copied or refreshed into runtime cores by `demiurge init`.

## Global Fallback Agent Config

`~/.demiurge/agents/agent.yaml` is the global fallback layer. It may contain
`model`, `ui`, and `approval`.

It must not contain concrete agent-bound fields such as `agent`, `slots`,
`tools`, `channels`, or `capabilities`.

## Concrete Core Config

Concrete agent cores configure core-owned runtime behavior:

```yaml
runtime:
  max_model_steps: 90
  workspace: /path/to/project
model:
  provider: deepseek
  model_name: deepseek-v4-flash
```

`max_model_steps` supports `1..90`. The default and hard limit are both `90`.
Provider endpoints and API keys do not belong in concrete core config.

## Success Check

```bash
uv run demiurge --provider fake
```

Use `/status` to inspect config sources for home, workspace, provider, model, API key,
tool display, UI settings, and debug switches.

## Reference

See [../reference/agent-yaml.md](../reference/agent-yaml.md) for YAML fields and
[../concepts/security-model.md](../concepts/security-model.md) for approval
policy behavior.
