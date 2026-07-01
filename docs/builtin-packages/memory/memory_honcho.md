---
sidebar_position: 2
title: memory_honcho
description: Install and configure the built-in Honcho-backed memory package.
---

# memory_honcho

`memory_honcho` adds Honcho-backed persistent memory to an Agent Core. It
injects relevant memory context before model calls, mirrors completed turns to
Honcho after the assistant responds, and optionally exposes model-callable
`honcho_*` tools.

Use it when you want cross-session user or project recall backed by Honcho
instead of only the file-backed [`memory_basic`](memory_basic.md) package.

## What It Installs

The package installs package-owned authored components into the selected runtime
core:

```text
agent/lib/memory_honcho/
agent/bootstrap/memory_honcho/
agent/input/memory_honcho_recall/
agent/output/memory_honcho_sync/
agent/tools/honcho_profile/
agent/tools/honcho_search/
agent/tools/honcho_context/
agent/tools/honcho_reasoning/
agent/tools/honcho_conclude/
agent/skills/memory_honcho/
```

It also edits the slot pipelines:

```yaml
agent/bootstrap/pipeline.yaml:
  serial:
    - session_context
    - memory_honcho

agent/input/pipeline.yaml:
  serial:
    - memory_honcho_recall
    - base_input

agent/output/pipeline.yaml:
  serial:
    - base_output
  parallel:
    - memory_honcho_sync
```

Runtime memory data is not package-owned. It is stored under:

```text
memory/honcho/
  cache.json
  outbox.jsonl
  synced_turns.json
```

Uninstall removes the installed slots, tools, skill, and lib files, but leaves
`memory/honcho/` in place.

## Requirements

`memory_honcho` declares a manual dependency:

```text
honcho-ai
```

Demiurge packages do not install Python dependencies and do not modify
`uv.lock`. Install `honcho-ai` according to the host environment policy before
using the package.

The package can connect to Honcho Cloud or to a self-hosted Honcho endpoint:

| Setup | Required configuration |
| --- | --- |
| Honcho Cloud | `HONCHO_API_KEY` or `api_key` package option |
| Self-hosted or local Honcho | `HONCHO_BASE_URL` or `base_url` package option |
| Self-hosted with auth | `base_url` plus `api_key` |

When `base_url` is set without `api_key`, the package passes `api_key="local"`
to the Honcho SDK.

## Install

Preview first:

```bash
uv run demiurge package install memory_honcho --core assistant --preview
```

Install with environment variables:

```bash
export HONCHO_API_KEY=...
uv run demiurge package install memory_honcho --core assistant
```

For a local or self-hosted Honcho service:

```bash
export HONCHO_BASE_URL=http://localhost:8000
uv run demiurge package install memory_honcho --core assistant
```

You can also pass options at install time:

```bash
uv run demiurge package install memory_honcho \
  --core assistant \
  --option api_key=... \
  --option workspace=demiurge \
  --option peer_name=allen \
  --option session_strategy=per-directory
```

Secret options are redacted in `packages.yaml`.

## Recall Modes

`recall_mode` controls whether memory is injected automatically, exposed through
tools, or both.

| Mode | Behavior |
| --- | --- |
| `hybrid` | Default. Injects Honcho context automatically and installs `honcho_*` tools when `enable_tools=true`. |
| `context` | Injects Honcho context automatically. Tools are still installed unless `enable_tools=false`; use that option if you want context-only behavior. |
| `tools` | Does not auto-inject Honcho context. Installs tools when `enable_tools=true`. |

To install tools-only mode:

```bash
uv run demiurge package install memory_honcho \
  --core assistant \
  --option recall_mode=tools
```

To disable tool installation:

```bash
uv run demiurge package install memory_honcho \
  --core assistant \
  --option enable_tools=false
```

## Options

| Option | Default | Description |
| --- | --- | --- |
| `recall_mode` | `hybrid` | `hybrid`, `context`, or `tools`. |
| `enable_tools` | `true` | Installs `honcho_*` authored tools when true. |
| `api_key` | unset | Direct Honcho API key. Falls back to `HONCHO_API_KEY`. |
| `base_url` | unset | Honcho API base URL. Falls back to `HONCHO_BASE_URL`. Use this for self-hosted or local Honcho. |
| `workspace` | `demiurge` | Honcho workspace id. |
| `peer_name` | unset | Stable user peer id. If unset, Demiurge derives one from turn metadata or session id. |
| `ai_peer` | `demiurge-assistant` | Honcho peer id for the assistant. |
| `session_strategy` | `per-directory` | Maps Demiurge turns to Honcho sessions. Choices: `per-directory`, `per-repo`, `per-session`, `global`. |
| `context_tokens` | `1200` | Parsed as the intended context budget. Current formatting is section-based and does not yet enforce a hard token truncation. |
| `timeout_seconds` | `3` | Passed to the Honcho SDK when supported. |
| `context_cadence` | `1` | Accepted for package configuration. The current implementation does not yet enforce a turn-gap throttle. |

## Session Mapping

The package maps each turn to a Honcho session with `session_strategy`:

| Strategy | Honcho session id |
| --- | --- |
| `per-directory` | Current workspace directory basename. |
| `per-repo` | Current workspace directory basename. This currently behaves like `per-directory`. |
| `per-session` | Demiurge session id. |
| `global` | The configured `workspace` value. |

Use `peer_name` when one human should keep the same Honcho peer across sessions
and workspaces. Use `ai_peer` when multiple Agent Cores should have distinct
assistant identities in the same Honcho workspace.

## Runtime Behavior

`memory_honcho` uses three slots around the model call:

| Slot | Timing | Behavior |
| --- | --- | --- |
| `bootstrap/memory_honcho` | Session bootstrap | Adds static `# Honcho Memory` guidance and any non-stale cached Honcho context. It does not fetch remote context. |
| `input/memory_honcho_recall` | Before `base_input` | In `hybrid` and `context` modes, fetches current Honcho context and injects it as transient system input. |
| `output/memory_honcho_sync` | Parallel output | Appends the completed turn to `outbox.jsonl`, drains pending outbox records to Honcho, and refreshes `cache.json` for the next turn. |

Injected memory is wrapped in `<memory-context>` and marked as background data,
not new user input. Completed turns are sanitized before syncing so leaked
`<memory-context>` blocks are not written back to Honcho.

All slots use `failure_policy: soft`. If Honcho is missing, misconfigured, slow,
or unavailable, Demiurge continues the main turn. The output slot keeps pending
records in `outbox.jsonl` so later successful runs can drain them.

## Tools

When `enable_tools=true`, the package installs five authored tools:

| Tool | Approval | Use |
| --- | --- | --- |
| `honcho_profile` | `auto` | Read or replace a peer card. Omit `card` to read; pass `card` as a list of strings to replace it. |
| `honcho_search` | `auto` | Search Honcho memory for raw context about a peer. Requires `query`. |
| `honcho_context` | `auto` | Fetch the current session summary, representation, and peer card. |
| `honcho_reasoning` | `auto` | Ask Honcho for a synthesized answer. Requires `query`; accepts optional `reasoning_level`. |
| `honcho_conclude` | `prompt` | Write or delete a persistent conclusion. Pass exactly one of `conclusion` or `delete_id`. |

All tools require `network.fetch`, have `risk: medium`, and return model-visible
JSON content. Each tool accepts optional `peer`, where `user` maps to the user
peer and `ai` or `assistant` maps to the assistant peer.

## Verify

List installed packages:

```bash
uv run demiurge package list --core assistant
```

Run a fake-provider turn to verify the core still loads:

```bash
uv run demiurge --provider fake
```

Inside the TUI, inspect tools with:

```text
/tools
```

After a real turn with Honcho configured, inspect:

```text
~/.demiurge/agents/assistant/memory/honcho/cache.json
~/.demiurge/agents/assistant/memory/honcho/synced_turns.json
```

If Honcho is unavailable, `outbox.jsonl` may exist until a later run drains it.

## Uninstall

Preview removal:

```bash
uv run demiurge package uninstall memory_honcho --core assistant --preview
```

Uninstall:

```bash
uv run demiurge package uninstall memory_honcho --core assistant
```

Uninstall restores the bootstrap, input, and output pipelines and removes the
package-owned component directories. It does not remove `memory/honcho/`.

## Difference From Hermes Honcho

This package follows the same broad shape as Hermes Honcho memory: static memory
guidance, automatic recall, completed-turn sync, and explicit Honcho tools.

The implementation boundary is different:

- Demiurge uses package-owned slots and lib code. It does not add host harness
  lifecycle hooks.
- There is no `hermes memory setup` equivalent. Configure the package through
  package options, environment variables, and the installed `config.yaml`.
- The package does not install `honcho-ai`.
- Bootstrap uses cached context only. Remote recall happens in the input slot.
- Turn sync uses a local durable outbox because package slots do not own a
  long-lived provider thread.
