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

## Model Fields

Core model config selects a host provider profile and model name:

```yaml
model:
  provider: deepseek
  model_name: deepseek-v4-pro
  model_options: {}
```

`model.provider` is a provider profile id from host config, or `auto`/`fake`.
`model.model_name` is a free-form provider model id. Provider endpoints and API
keys belong in `~/.demiurge/config.yaml` provider profiles, not in
`agent.yaml`.

## Channel Fields

External channel configs live under `channels.<name>`. Supported names are
`telegram`, `webhook`, `slack`, `mattermost`, `matrix`, and `email`. Each channel
is disabled by default and should read secrets from environment variables.

```yaml
channels:
  telegram:
    enabled: true
    bot_token_env: DEMIURGE_TELEGRAM_BOT_TOKEN
    allowed_users: [123456789]
    allowed_chats: []
    reply_to_mode: "off"
  webhook:
    enabled: false
    token_env: DEMIURGE_WEBHOOK_TOKEN
    host: 127.0.0.1
    port: 8765
    path: /demiurge
    delivery_targets: {}
  slack:
    enabled: false
    bot_token_env: SLACK_BOT_TOKEN
    signing_secret_env: SLACK_SIGNING_SECRET
    host: 127.0.0.1
    port: 8766
    path: /slack/events
    allowed_channels: []
  mattermost:
    enabled: false
    base_url: https://mattermost.example.com
    token_env: MATTERMOST_BOT_TOKEN
    webhook_token_env: MATTERMOST_WEBHOOK_TOKEN
    allowed_channels: []
  matrix:
    enabled: false
    homeserver_url: https://matrix.example.org
    access_token_env: MATRIX_ACCESS_TOKEN
    user_id: "@demiurge:example.org"
    allowed_rooms: []
  email:
    enabled: false
    smtp_host: smtp.example.com
    imap_host: imap.example.com
    smtp_username_env: DEMIURGE_SMTP_USERNAME
    smtp_password_env: DEMIURGE_SMTP_PASSWORD
    imap_username_env: DEMIURGE_IMAP_USERNAME
    imap_password_env: DEMIURGE_IMAP_PASSWORD
    allowed_senders: []
    allowed_recipients: []
```

Schedules use `delivery.mode: telegram` plus `chat_id` for Telegram, or
`delivery.mode: <channel>` plus `target` for the other external channels.

## Boundary

`dependencies.mode` defaults to `host_shared`. Candidate cores cannot
automatically add Python dependencies.
