---
title: Configure a Provider
description: Create a provider profile, choose the model used by a core, and verify a live provider.
---

# Configure a Provider

Use the fake provider until the TUI starts locally. Then add a real provider
profile in host config and point your runtime core at a model.

For a managed install, replace `uv run demiurge` with:

```bash
~/.demiurge/demiurge-agent/.venv/bin/demiurge
```

## 1. Open the Wizard

Run setup without a setup subcommand:

```bash
uv run demiurge setup
```

The wizard can create provider profiles, write secrets to `~/.demiurge/.env`,
choose a host default provider, and set a core model.

## 2. Inspect Current State

Use this before and after changes:

```bash
uv run demiurge setup status
uv run demiurge setup status --json
```

`setup status` reports secret sources, not secret values.

## 3. Add a Provider Profile

If your provider matches a built-in preset, start from that preset:

```bash
uv run demiurge setup providers add <provider-id> \
  --preset <preset-id> \
  --api-key-env <API_KEY_ENV> \
  --set-default
```

If your provider is a custom OpenAI-compatible endpoint, provide the base URL:

```bash
uv run demiurge setup providers add <provider-id> \
  --base-url https://<provider-host>/v1 \
  --api-key-env <API_KEY_ENV> \
  --set-default
```

Provider profiles also choose an `api_mode`, which controls the host-owned wire
protocol adapter. Built-in OpenAI-compatible presets default to `openai-chat`.
Use `anthropic-messages` only for endpoints that speak the Anthropic Messages
API:

```bash
uv run demiurge setup providers add anthropic \
  --api-mode anthropic-messages \
  --base-url https://api.anthropic.com/v1 \
  --api-key-env ANTHROPIC_API_KEY \
  --set-default
```

Export the secret in your shell or store it in `~/.demiurge/.env`:

```bash
export <API_KEY_ENV>=<api-key>
```

To let setup write the provided key into the runtime `.env` file:

```bash
uv run demiurge setup providers add <provider-id> \
  --preset <preset-id> \
  --api-key "$<API_KEY_ENV>" \
  --write-env \
  --set-default
```

## 4. Set the Core Model

Set the model used by the `assistant` core:

```bash
uv run demiurge setup model set \
  --core assistant \
  --provider <provider-id> \
  --model <model-name>
```

Use the model name expected by your provider. Do not commit secrets or local
provider choices unless you intend them to be shared.

## 5. Test and Run

Run an explicit provider test:

```bash
uv run demiurge setup providers test <provider-id> --model <model-name>
```

Then start the TUI with that provider:

```bash
uv run demiurge --provider <provider-id>
```

If startup fails, verify that the fake provider still works:

```bash
uv run demiurge --provider fake
```

## Provider Resolution Order

Demiurge chooses a provider in this order:

1. CLI override such as `--provider <provider-id>`.
2. The selected runtime core manifest.
3. The global fallback manifest.
4. The host default provider.
5. `fake`.

Use `--provider fake` whenever you need to separate runtime problems from live
provider problems.

## Useful Commands

```bash
uv run demiurge setup providers list
uv run demiurge setup providers show <provider-id>
uv run demiurge setup providers edit <provider-id> --base-url https://<provider-host>/v1
uv run demiurge setup providers set-default <provider-id>
uv run demiurge setup providers remove <provider-id>
uv run demiurge setup timezone set <IANA-timezone>
uv run demiurge setup timezone clear
```

## Boundary and Secrets

Provider profiles are host-owned configuration. Agent Cores can declare model
defaults in `agent.yaml`, but Agent Slots should not construct provider requests
or read secrets directly. The host resolves the profile, selects the
`api_mode`, converts the internal `LLMRequest` into the provider-native payload,
and normalizes the response back into `LLMResponse`. Prefer environment
variables or `~/.demiurge/.env` for API keys.
