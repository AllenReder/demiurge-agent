# Configuration

## Runtime Home

The default runtime home is `~/.demiurge`. Override it with a CLI flag or environment variable:

```bash
uv run demiurge --home ./.demiurge
DEMIURGE_HOME=./.demiurge uv run demiurge
```

## Host Runtime Config

`<home>/config.yaml` is host-level configuration. The default path is `~/.demiurge/config.yaml`. `demiurge init` creates it if missing; normal startup reads it and does not overwrite it.

This file stores local host preferences. It is not an agent core and not the global fallback agent config.

```yaml
runtime:
  default_core: assistant
  workspace: null
channel:
  busy_mode: interrupt
ui:
  user_message_align: left
  demiurge_theme_color: ff9afc
  user_theme_color: 9cc9ff
```

- `runtime.default_core`: used when `--core` is omitted. Default: `assistant`.
- `runtime.workspace`: used when neither `--workspace` nor `DEMIURGE_WORKSPACE` is set. If null, demiurge uses `<home>/workspace`.
- `channel.busy_mode`: initial in-flight input behavior for interactive channels. Values: `interrupt` or `queue`.
- `ui.user_message_align`: local TUI user message alignment. Values: `left` or `right`.
- `ui.demiurge_theme_color`: local TUI demiurge identity/status color. Accepts `ff9afc`, `#ff9afc`, `fac`, or `#fac`.
- `ui.user_theme_color`: local TUI user identity color. Same color format.

Precedence:

- `--core` overrides `runtime.default_core`.
- `--workspace` overrides `DEMIURGE_WORKSPACE`.
- `DEMIURGE_WORKSPACE` overrides `runtime.workspace`.
- `/busy` changes only the current running channel process and does not write config.

Invalid values fail startup and include the offending field path in the error.

## Workspace

File and terminal tools can only access the configured workspace. The default is:

```text
~/.demiurge/workspace
```

Override it:

```bash
uv run demiurge --workspace /path/to/project
DEMIURGE_WORKSPACE=/path/to/project uv run demiurge
```

## Source Agents Root

Source templates default to the repository `agents/` directory in a checkout, or to bundled package resources in a wheel install. Override the source agents root:

```bash
uv run demiurge --agents-root /path/to/agents
DEMIURGE_AGENTS_ROOT=/path/to/agents uv run demiurge
```

Runtime cores live under `<home>/agents/`. Source templates are copied or refreshed into runtime cores by `demiurge init`.

## Global Fallback Agent Config

`~/.demiurge/agents/agent.yaml` is the global config and fallback layer for runtime agents. It allows top-level `model`, `ui`, and `approval`.

It must not contain concrete agent-bound fields such as `agent`, `slots`, `tools`, or `capabilities`.

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

Model resolution order:

1. CLI overrides
2. current agent core `*_env` values
3. current agent core direct values
4. global fallback `*_env` values
5. global fallback direct values
6. standard environment fallback
7. fake default model

Do not store real API keys in versioned agent cores. Prefer `api_key_env`.

Tool display resolution order:

1. CLI `--tool-display`
2. current agent core `ui.tool_display`
3. global `agents/agent.yaml` `ui.tool_display`
4. default `summary`

Allowed values: `quiet`, `summary`, `full`.

TUI and Telegram both use this value. TUI can switch at runtime with:

```text
/tool-display quiet|summary|full
```

Telegram resolves the value at channel startup.

## Core Runtime Config

Concrete agent cores can configure the single-turn model loop budget:

```yaml
runtime:
  max_model_steps: 90
```

Default and hard limit are both `90`. Allowed range is `1..90`; invalid values fail core load.

This field belongs to a concrete core, not to the root `agents/agent.yaml` fallback.

## Approval Config

Approval resolution rules:

- Agent cores can make policy stricter, for example by denying a tool.
- Agent cores cannot lower the host security baseline into automatic allow.
- Global `agents/agent.yaml` represents user policy and can explicitly set ordinary prompt tools to `auto`, `prompt`, or `deny`.
- Workspace escapes, undeclared capabilities, and unknown tools are never allowed by config.

## Channel Config

Channel config belongs in a concrete agent core `agent.yaml`. The host reads it and owns the adapters. TUI is still the default local entry; `demiurge gateway` starts enabled external channels for the selected core.

v1 external channels support Telegram:

```yaml
channels:
  telegram:
    enabled: true
    bot_token_env: DEMIURGE_TELEGRAM_BOT_TOKEN
    bot_token: null
    bot_username: your_bot
    allowed_users:
      - 123456789
    allowed_chats:
      - -1001234567890
    unauthorized_response: brief
    poll_timeout: 30
    message_format: markdown_v2
    register_commands: true
    send_typing: true
    rich_messages: true
    reply_to_mode: "off"
```

`uv run demiurge --core assistant` starts the local TUI and does not read external channel enabled state.

`uv run demiurge gateway --core assistant` starts enabled external channels. It errors when none are enabled.

`bot_token_env` is preferred; `bot_token` is a plaintext fallback. Avoid putting real tokens into runtime core files, history, or candidate diffs.

Telegram authorization uses numeric IDs only:

- private chats require `from.id` in `allowed_users`;
- group/supergroup messages require both `chat.id` in `allowed_chats` and sender `from.id` in `allowed_users`.

`reply_to_mode` controls Telegram reply references only: `off`, `first`, or `all`. It does not affect session routing.
