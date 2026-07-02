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

`ToolRuntime` does not own per-tool background state. Background-capable tools
submit work to the shared `JobRuntime`:

- `terminal(background=true)` creates a `terminal` backend job and captures
  stdout/stderr into the job log.
- `run_terminal(...)` is a model-facing alias that defaults terminal execution
  to `background=true`.
- `evolve_core(background=true)` creates an `evolve` backend job and runs with
  `auto_promote=false`; it produces a candidate and report but does not switch
  the active core.
- `ctx.agents.spawn(...)` is routed by the runner into an `agent` backend job.
- `delegate_task(...)` routes through the runner delegation adapter and creates
  an `agent` backend job with child output returned as parent evidence.

`job` is the compatibility control tool for `list`, `poll`, `log`, `wait`, and
`cancel`. `task_status`, `task_control`, and `yield_until` are the model-facing
runtime-task controls. `process` remains only as a compatibility view over
terminal jobs. Active execution is still in-process, while task and log
projections are mirrored into SQLite through `RuntimeControlPlane`.

Each job records `backend`, owner session/turn, `source_tool`, status, summary,
bounded log tail, result reference, and an optional `write_scope`. A new active
background job with the same non-empty `write_scope` is rejected.

## Authored Tools

Authored tools are adapters. They use the same host-owned dispatch path as
built-ins after discovery.

## MCP Tools

MCP tools are namespaced and filtered to avoid collisions. Transport, discovery,
timeouts, and result conversion are host-owned.

## Boundary

The Agent Core can declare tools. It does not own tool-call replay,
authorization, or provider-specific tool message formatting.
