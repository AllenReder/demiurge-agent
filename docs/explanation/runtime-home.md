---
title: Runtime Home
description: Understand the local runtime directory layout under ~/.demiurge.
---

# Runtime Home

Demiurge is local-first. Runtime state lives under a runtime home, usually:

```text
~/.demiurge
```

The source checkout and runtime home have different roles.

## Main Layout

```text
~/.demiurge/
  config.yaml
  .env
  agents/
    agent.yaml
    assistant/
    evolver/
  runtime/
    runtime.sqlite3
    artifacts/
    session-events/
  workspace/
  logs/
```

`config.yaml` is host-owned runtime configuration. `.env` can hold local
provider secrets. `agents/` contains live runtime Agent Cores. `runtime/`
contains the SQLite control-plane database, delivery outbox projection,
scheduler runtime projections, session event logs, and host-owned artifacts.
`workspace/` is the non-local fallback workspace.

## Source Templates vs Runtime Cores

The repository contains source templates under:

```text
agents/
```

`demiurge init` copies or refreshes those templates into:

```text
~/.demiurge/agents/
```

Edit runtime cores for local behavior changes. Edit source templates only when
you are changing the default packaged project behavior.

## Managed Checkout

Managed install places the checkout at:

```text
~/.demiurge/demiurge-agent
```

Live runtime cores remain separate, so updating the managed checkout does not
overwrite edited Agent Cores.

## Drift

Use read-only drift checks before refreshing runtime files:

```bash
uv run demiurge init --check
uv run demiurge doctor
```

Refresh intentionally:

```bash
uv run demiurge init --refresh assistant
```
