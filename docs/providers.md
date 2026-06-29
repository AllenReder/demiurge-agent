# Providers

demiurge currently supports a deterministic fake provider and an
OpenAI-compatible Chat Completions provider.

## Fake Provider

The fake provider is used for local tests and gates. It requires no network and
no API key:

```bash
uv run demiurge --provider fake
```

`--provider auto` is the default. If no real provider configuration is
available, demiurge falls back to the fake provider so local startup remains
possible.

Scripted fake responses can be provided with:

```bash
uv run demiurge --provider fake --fake-script path/to/script.json
```

## OpenAI-Compatible Provider

Use a real provider with:

```bash
export DEMIURGE_MODEL_NAME="gpt-4.1-mini"
export DEMIURGE_BASE_URL="https://api.openai.com/v1"
export DEMIURGE_API_KEY="..."
uv run demiurge --provider openai
```

Compatible services can use a different base URL:

```bash
uv run demiurge \
  --provider openai-compatible \
  --model deepseek-v4-flash \
  --base-url https://example.com/v1 \
  --api-key "$DEMIURGE_API_KEY"
```

## Configuration Sources

Model name, base URL, and API key can come from CLI flags, the current agent
core, global fallback config, or environment variables. `/status` shows the
source of each value without printing API key contents.

Resolution order:

1. CLI overrides.
2. Current core `*_env` values.
3. Current core direct values.
4. Global fallback `*_env` values.
5. Global fallback direct values.
6. Standard environment fallback.
7. Internal fake default.

Prefer `api_key_env` and environment variables for real secrets. Do not commit
API keys into versioned agent cores.
