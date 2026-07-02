---
title: Tool Runtime
description: Contributor notes for tool discovery, metadata, dispatch, approvals, and results.
---

# Tool Runtime

The tool runtime builds the visible tool registry and executes calls.

## Registry Sources

Tools can come from:

- built-in toolsets
- authored tools under `agent/tools/`
- MCP tools discovered from `agent/mcp/*.yaml`

`agent.yaml` chooses built-in toolsets and can override tool metadata.

## Dispatch

The runtime:

1. resolves the tool registry entry
2. checks enabled state
3. applies capability and approval policy
4. enforces workspace and safety rules where relevant
5. executes the built-in, authored, or MCP tool
6. converts the result for model history and user display

## Background Jobs

`ToolRuntime` does not own background state. Background-capable tools submit
typed actions to the host runtime and use the shared `RuntimeTaskWorker` as the
in-process backend for active work:

- `terminal(background=true)` creates a `terminal.exec` task and captures
  stdout/stderr into `task_logs`.
- `run_terminal(...)` is a model-facing alias that defaults terminal execution
  to `background=true`.
- `evolve_core(background=true)` creates an `evolver.run` task and runs with
  `auto_promote=false`; it produces a candidate and report but does not switch
  the active core.
- `ctx.agents.spawn(...)` is routed by the runner into an `agent.spawn` task.
- `delegate_task(...)` is executed by the active runner context and creates an
  `agent.spawn` task with child output returned as parent evidence.

`job` is the compatibility control tool for `list`, `poll`, `log`, `wait`, and
`cancel`. `task_status`, `task_control`, and `yield_until` are the model-facing
runtime-task controls. `process` remains only as a compatibility view over
terminal tasks. Active execution is still in-process, while task status and logs
are read from SQLite projections through `RuntimeControlPlane`.

Each background task records `backend`, owner session/turn, `source_tool`,
status, summary, bounded log tail, result reference, and an optional
`write_scope`. A new active background task with the same non-empty
`write_scope` is rejected.

## Authored Tools

Authored tools are adapters. They use the same host-owned dispatch path as
built-ins after discovery.

## MCP Tools

MCP tools are namespaced and filtered to avoid collisions. Transport, discovery,
timeouts, and result conversion are host-owned.

## Boundary

The Agent Core can declare tools. It does not own tool-call replay,
authorization, or provider-specific tool message formatting.
