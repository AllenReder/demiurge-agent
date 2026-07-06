---
title: Architecture
description: Map the current Demiurge host runtime for contributors.
---

# Architecture

This guide maps the current implementation. It is not a stable plugin API.

## System Overview

```text
CLI / TUI / Gateway
        |
        +--> OperatorGatewayRuntime
        |
        v
create_app()
        |
        v
SessionTurnStepRunner
        |
        +--> ContextAssembler
        +--> Provider
        +--> ToolRuntime
        +--> CoreRepository / EvolutionRuntime / GateRunner
        +--> RuntimeControlPlane / RuntimeStore
        +--> HostWorkLifecycleRuntime
        +--> RuntimeTaskWorker
        +--> DeliveryRuntime
        +--> SessionRuntime
        +--> SchedulerService
```

## Major Subsystems

| Subsystem | Responsibility |
| --- | --- |
| CLI | Parse commands and start TUI, gateway, setup, package, update, and doctor flows. |
| App factory | Resolve runtime home, config, source templates, core, workspace, provider, approvals, and tools. |
| Core loader | Load `agent.yaml`, slots, pipelines, skills, schedules, and MCP declarations. |
| Runner | Wire host runtime modules for turn admission, authored pipeline execution, persistence, tools, slots, and delivery. |
| Context assembler | Build provider messages from soul, skills, bootstrap, input, history, and current turn. |
| Tool runtime | Build the visible registry and execute built-in, authored, and MCP tools. |
| Core repository | Own the Git-backed runtime agents tree, refs, change sets, package transactions, and rollback commits. |
| Evolution runtime | Start, review, promote, and discard isolated agents-tree change sets through the core repository and gates. |
| Gate runner | Run host-owned checks for path safety, artifacts, dependency files, core loading, package provenance, drift warnings, and cross-core references. |
| Runtime control plane | Submit detached task specs, project task events, and expose task/session/scheduler/outbox state from SQLite. |
| Host work lifecycle | Claim, complete, fail, cancel, acknowledge, and observe host-managed work across durable work items, detached tasks, delivery, schedules, and completion inboxes. |
| Operator gateway | Own local TUI/dashboard product state, operator events, prompts, approvals, status, history, and slash-command routing. |
| Runtime task worker | Hold active process handles and live completion subscribers; pending completions are recovered from SQLite events. |
| Delivery runtime | Dispatch authored delivery intents and update outbox status. |
| Session runtime | Read and write session, turn, message, bootstrap, and compaction projections. |
| Scheduler | Claim due schedules through SQLite scheduler projections and run fresh sessions. |
| Package manager | Preview, install, uninstall, and record package repository components. |

## Entry Points

- `demiurge/cli.py`
- `demiurge/app/__init__.py`
- `demiurge/runtime/runner.py`
- `demiurge/core_repository.py`
- `demiurge/evolution/__init__.py`
- `demiurge/gates/__init__.py`
- `demiurge/runtime/control.py`
- `demiurge/runtime/host_work.py`
- `demiurge/runtime/store.py`
- `demiurge/runtime/tasks.py`
- `demiurge/runtime/outbox.py`
- `demiurge/ui_gateway/bridge.py`
- `demiurge/ui_gateway/entry.py`
- `demiurge/tools/runtime.py`
- `demiurge/channels/gateway.py`
- `demiurge/packages.py`
- `demiurge/scheduler/__init__.py`

## Reading Order

1. [runner-and-context.md](runner-and-context.md)
2. [tool-runtime.md](tool-runtime.md)
3. [delivery-runtime.md](delivery-runtime.md)
4. [package-installer.md](package-installer.md)
5. [scheduler.md](scheduler.md)
6. [operator-gateway.md](operator-gateway.md)
7. [mcp-runtime.md](mcp-runtime.md)

## Boundary

When a runtime change affects user-visible behavior, CLI/configuration, package
recipes, runtime layout, security policy, provider behavior, state/versioning,
or test/gate workflow, update the public manual in the same change.
