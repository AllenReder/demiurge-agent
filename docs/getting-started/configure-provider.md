# Configure a Provider

Demiurge currently uses an OpenAI-compatible Chat Completions adapter for real
model calls. Provider connection details are host-owned and live in
`~/.demiurge/config.yaml`; agent cores only select a provider profile and model
name.

## Fake Provider

The fake provider is the safest first check:

```bash
uv run demiurge --provider fake
```

It requires no network and no API key. Use it to verify runtime initialization,
TUI startup, sessions, tools, and output modules.

## Interactive Setup

Run:

```bash
uv run demiurge setup
```

The setup menu can create provider profiles for OpenAI, DeepSeek,
Kimi/Moonshot, MiniMax, Alibaba DashScope/百炼, Zhipu/Z.ai, SiliconFlow,
OpenRouter, or a custom OpenAI-compatible endpoint. Model names are free-form:
the presets provide editable suggestions, not a maintained model catalog.

## Scripted Setup

Example:

```bash
uv run demiurge setup providers add openai --preset openai --set-default
uv run demiurge setup model set --core assistant --provider openai --model gpt-4.1-mini
uv run demiurge --provider openai
```

Store a secret in the runtime env file:

```bash
uv run demiurge setup providers add deepseek \
  --preset deepseek \
  --api-key-env DEEPSEEK_API_KEY \
  --api-key "..." \
  --write-env \
  --set-default
```

`--write-env` writes the key to `~/.demiurge/.env` and keeps the profile
pointing at `api_key_env` rather than storing the direct key in `config.yaml`.

## Host Config Shape

Provider profiles live in `~/.demiurge/config.yaml`:

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

Core model defaults live in the concrete core:

```yaml
model:
  provider: deepseek
  model_name: deepseek-v4-flash
  model_options: {}
```

Do not put provider endpoints or API keys in `agent.yaml`.

## Configuration Sources

Provider profile selection resolves in this order:

1. CLI `--provider`.
2. Current concrete core `model.provider`.
3. Global fallback `agents/agent.yaml` `model.provider`.
4. Host `providers.default`.
5. Fake provider.

Model names resolve in this order:

1. CLI `--model`.
2. Current concrete core `model.model_name`.
3. Global fallback `agents/agent.yaml` `model.model_name`.
4. Fake default model.

API keys resolve in this order:

1. Explicit runtime override used by internal setup checks.
2. `api_key_env`, loaded from `~/.demiurge/.env` or the shell.
3. Direct `api_key` in `config.yaml`.

Values in `~/.demiurge/.env` override existing shell environment variables for
Demiurge runtime resolution.

## Checks

Inspect provider setup:

```bash
uv run demiurge setup status --json
uv run demiurge setup providers list --json
```

Run an explicit live provider test only when you want to make a network call:

```bash
uv run demiurge setup providers test deepseek --model deepseek-v4-flash
```

Then use `/status` in the TUI. It should show the provider profile, model,
base URL source, and API key source. It must not print the API key value.

## Failure Modes

- Missing API key: set the configured `api_key_env` in the shell or
  `~/.demiurge/.env`, or store a direct `api_key` in the profile.
- Wrong endpoint: edit the provider profile with `demiurge setup providers edit`.
- Unknown provider: create it with `demiurge setup providers add`, or use one of
  the built-in preset ids.
- Provider error after startup: verify the model name and endpoint outside
  Demiurge, then rerun with the same profile.

See [../operations/troubleshooting.md](../operations/troubleshooting.md).
