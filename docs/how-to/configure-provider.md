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

## 3. Add a Provider

If your provider matches a built-in preset, start from that preset:

```bash
uv run demiurge setup providers add <provider-id> \
  --preset <preset-id> \
  --set-default
```

Built-in providers own their standard base URL, default environment variable,
and wire protocol inside the Demiurge provider profile. Use the provider's
official API key environment variable, such as `OPENAI_API_KEY`,
`ANTHROPIC_API_KEY`, `DEEPSEEK_API_KEY`, or `MINIMAX_API_KEY`; Demiurge does
not add a `DEMIURGE_` prefix for provider request credentials.

Only set `--base-url` for a built-in provider when you intentionally route it
through a proxy or regional endpoint. Built-in provider config has no
`api_key_env`, `api_key`, or `api_mode` override fields:

```bash
uv run demiurge setup providers edit deepseek \
  --base-url https://<provider-host>/v1
```

Built-in providers do not accept `--api-mode` overrides. If you need a different
wire protocol, configure a custom provider. Custom providers must provide a base
URL and may choose `openai-chat` or `anthropic-messages`:

```bash
uv run demiurge setup providers add local-anthropic \
  --base-url https://llm.example.test/v1 \
  --api-mode anthropic-messages \
  --api-key-env LOCAL_ANTHROPIC_API_KEY \
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
  --api-key "<api-key>" \
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

## Host Config Shape

Provider config is host-owned and intentionally sparse:

```yaml
providers:
  default: deepseek
  builtin:
    deepseek:
      base_url: https://proxy.example.test/v1
  custom:
    local-openai:
      base_url: http://localhost:11434/v1
      api_mode: openai-chat
      api_key_env: LOCAL_OPENAI_API_KEY
```

`providers.builtin.<id>` can only override `base_url`.
`providers.custom.<id>` requires `base_url` and may set `api_mode`,
`api_key_env`, or `api_key`. The old `providers.profiles` map is not supported
by this schema.

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
or read secrets directly. The host resolves the provider profile, provider
transports convert the internal `LLMRequest` into provider-native payloads, and
responses are normalized back into `LLMResponse`. Prefer environment variables
or `~/.demiurge/.env` for API keys.
