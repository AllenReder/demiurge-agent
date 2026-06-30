# `agent.yaml` Reference

There are two different `agent.yaml` roles.

## Global Fallback Config

Path:

```text
~/.demiurge/agents/agent.yaml
```

Allowed top-level fields:

- `model`
- `ui`
- `approval`

Do not put concrete agent-bound fields here.

Example:

```yaml
model:
  provider: auto
  model_name: null
  model_name_env: DEMIURGE_MODEL_NAME
  base_url: null
  base_url_env: DEMIURGE_BASE_URL
  api_key: null
  api_key_env: DEMIURGE_API_KEY
  model_options: {}
ui:
  tool_display: summary
approval:
  tools:
    terminal: prompt
```

## Concrete Core Config

Path:

```text
~/.demiurge/agents/<core>/agent.yaml
```

Common top-level fields:

- `schema_version`
- `agent`
- `runtime`
- `model`
- `ui`
- `channels`
- `slots`
- `tools`
- `approval`
- `capabilities`
- `dependencies`
- `tests`

Minimal shape:

```yaml
schema_version: 1
agent:
  id: assistant
  version: 0.1.0
  summary: Local assistant
runtime:
  surface_root: agent
  max_model_steps: 90
slots:
  input: agent/input
  output: agent/output
  tools: agent/tools
  skills: agent/skills
tools:
  toolsets:
    - coding
    - demiurge_control
capabilities:
  defaults: {}
dependencies:
  mode: host_shared
  allow_additional_dependencies: false
```

## Runtime Fields

`runtime.max_model_steps` supports `1..90`; default and hard limit are `90`.

`runtime.workspace` is optional. Relative paths resolve from the runtime core
directory. It is mainly for gateway, Telegram, scheduler, and other non-local
channel runs.

## Channel Fields

Current external channel support is Telegram:

```yaml
channels:
  telegram:
    enabled: true
    bot_token_env: DEMIURGE_TELEGRAM_BOT_TOKEN
    allowed_users: [123456789]
    allowed_chats: []
    reply_to_mode: "off"
```

## Boundary

`dependencies.mode` defaults to `host_shared`. Candidate cores cannot
automatically add Python dependencies.
