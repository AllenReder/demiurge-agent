# Runtime Layout Reference

## Source Checkout

```text
demiurge-agent/
  agents/
    agent.yaml
    assistant/
    evolver/
  package-repository/
  demiurge/
  docs/
  ui-tui/
```

`agents/` contains source templates. `package-repository/` contains the built-in
package repository: reusable package components and package recipes.

## Runtime Home

```text
~/.demiurge/
  config.yaml
  agents/
  registry/
  sessions/
  scheduler/
  logs/
  workspace/
  history/
  package-repositories/
```

## Runtime Core

```text
~/.demiurge/agents/assistant/
  agent.yaml
  packages.yaml
  agent/
    SOUL.md
    bootstrap/
    input/
    output/
    tools/
    skills/
    schedules/
    mcp/
    lib/
    tests/
```

## Session

```text
~/.demiurge/sessions/<session_id>/
  session.json
  bootstrap_context.md
  messages.jsonl
  events.jsonl
  artifacts/
```

## Scheduler

```text
~/.demiurge/scheduler/<core_id>/
  state.json
  runs.jsonl
  lock
```

## MCP Logs

```text
~/.demiurge/logs/mcp-stderr.log
```

## Boundary

Runtime state files are host-owned. Authored behavior belongs in runtime cores,
not direct edits to session, registry, scheduler, or log files.
