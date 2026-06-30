# Configure a Provider

demiurge currently supports the fake provider and an OpenAI-compatible Chat
Completions provider. Provider selection is host-owned; agent cores can provide
defaults, but authored modules do not call model APIs directly.

## Fake Provider

The fake provider is the safest first check:

```bash
uv run demiurge --provider fake
```

It requires no network and no API key. Use it to verify runtime initialization,
TUI startup, sessions, tools, and output modules.

## OpenAI-Compatible Provider

Set secrets in environment variables:

```bash
export DEMIURGE_MODEL_NAME="gpt-4.1-mini"
export DEMIURGE_BASE_URL="https://api.openai.com/v1"
export DEMIURGE_API_KEY="..."
uv run demiurge --provider openai
```

Temporary overrides:

```bash
uv run demiurge \
  --provider openai \
  --model gpt-4.1-mini \
  --base-url https://api.openai.com/v1 \
  --api-key "$DEMIURGE_API_KEY"
```

`openai-compatible` is accepted as an alias for `openai`.

## Configuration Sources

Model values resolve in this order:

1. CLI overrides.
2. Current concrete agent core `*_env` values.
3. Current concrete agent core direct values.
4. Global fallback `agents/agent.yaml` `*_env` values.
5. Global fallback direct values.
6. Standard environment fallback.
7. Fake default model.

Core-local model defaults belong in a concrete core `agent.yaml`. Shared host
fallbacks belong in `~/.demiurge/agents/agent.yaml`.

## Success Check

Run:

```bash
uv run demiurge --provider openai
```

Then use `/status`. It should show the provider, model, base URL source, and API
key source. It must not print the API key value.

## Failure Modes

- Missing API key: set `DEMIURGE_API_KEY` or pass `--api-key`.
- Wrong endpoint: set `DEMIURGE_BASE_URL` or pass `--base-url`.
- Provider error after startup: verify the model name and endpoint outside
  demiurge, then rerun with the same env values.

See [../operations/troubleshooting.md](../operations/troubleshooting.md).
