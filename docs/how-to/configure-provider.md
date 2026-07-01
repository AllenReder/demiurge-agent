---
title: Configure a Provider
description: Create provider profiles and choose the model used by a core.
---

# Configure a Provider

Use the fake provider until the runtime works locally. Then configure a real
provider profile in host config.

## Interactive Setup

```bash
uv run demiurge setup
```

The setup flow can create provider profiles, write secrets to `~/.demiurge/.env`,
choose a default provider, and set a core model.

Inspect the result:

```bash
uv run demiurge setup status
uv run demiurge setup status --json
```

## Scripted Setup

Create an OpenAI profile and make it the host default:

```bash
uv run demiurge setup providers add openai --preset openai --set-default
```

Set the model for the `assistant` core:

```bash
uv run demiurge setup model set --core assistant --provider openai --model gpt-5.5
```

Run with that provider:

```bash
uv run demiurge --provider openai
```

## Secrets

Prefer environment variables or `~/.demiurge/.env` for secrets:

```bash
uv run demiurge setup providers add openai \
  --preset openai \
  --api-key-env OPENAI_API_KEY \
  --set-default
```

For local testing you can ask setup to write a provided key to `.env`:

```bash
uv run demiurge setup providers add openai \
  --preset openai \
  --api-key "$OPENAI_API_KEY" \
  --write-env \
  --set-default
```

`/status` and `setup status --json` report secret sources, not secret values.

## Useful Commands

```bash
uv run demiurge setup providers list
uv run demiurge setup providers edit openai --base-url https://api.openai.com/v1
uv run demiurge setup providers test openai --model gpt-5.5
uv run demiurge setup providers set-default openai
uv run demiurge setup model set --core assistant --provider openai --model gpt-5.5
uv run demiurge setup timezone set Asia/Shanghai
uv run demiurge setup timezone clear
```

## Boundary

Provider profiles are host-owned configuration. Agent Cores can declare model
defaults in `agent.yaml`, but Agent Slots should not construct provider requests
or read secrets directly.
