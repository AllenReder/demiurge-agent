---
title: Architecture
description: Map the current Demiurge host runtime for contributors.
---

# Architecture

This guide maps both the current alpha implementation and the Host runtime
interfaces that later hardening work must converge on. It is not a stable
plugin API.

## System Overview

The current implementation is runner-centered:

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

The frozen target ownership is module-centered:

```text
authenticated inbound
        |
        v
ChannelInbox --> TurnExecution --> ContextManager --> ProviderRuntime
                         |
                         +-------> EffectRuntime
                         +-------> SlotRuntime
                         +-------> DeliveryRuntime / durable task runtime
```

`PrincipalScope` and an immutable `TurnExecutionContext` carry authority and
turn identity across those Host interfaces. Agent Core authors still use the
smaller `ctx.*` SDK and Agent Slot interfaces; they do not call these Host
interfaces directly.

## Current and Target Contracts

The complete interface, ordering, error, cancellation, restart, performance,
and finding-owner rules are frozen in
[Host Runtime Contracts](runtime-contracts.md). In particular, see its
[primary finding owner map](runtime-contracts.md#primary-finding-owners) before
adding another policy or lifecycle owner.

| Contract | Current alpha implementation | Frozen target |
| --- | --- | --- |
| `PrincipalScope` | Immutable Host-derived authority, durable session-owner predicates, and approval-cache ownership/invalidation exist; remaining session/task/history consumers migrate in later DG-P2 work. | One immutable owner predicate reaches every session, task, approval, history, and effect operation. |
| `TurnExecutionContext` | Frozen turn identity pins principal, session, revision, capability declarations, route, cancellation, admission, and trace; mutable lifecycle/state handles remain internal. | Deeply immutable principal, session, revision, capability, route, admission, cancellation, and trace bindings. |
| `TurnExecution` | `run(TurnRequest)` owns admission through completion; owner-checked `cancel(...)`, captured-route delivery, concurrency, and cleanup are implemented. | Typed outcomes, durable admission/restart recovery, and final immutable result projections complete the lifecycle. |
| `EffectRuntime` | `ToolRuntime`, `McpRuntime`, security helpers, and inline file/process/network code own different parts of dispatch. | One resolved effect entry and one policy/dispatch order for builtin, authored, and MCP effects. |
| `ContextManager` | Layer assembly and manual compaction are separate; there is no automatic model-window budget. | `prepare()` and `observe()` own budgeting, pruning, compaction, usage calibration, and overflow semantics. |
| `ChannelInbox` | Platform adapters pass inbound events directly to the runner; there is no shared durable inbound owner. | Durable accept, dedup, cursor, claim, complete/fail, retry, and DLQ semantics behind one interface. |

`EffectRuntime`, `ContextManager`, and `ChannelInbox` remain target contributor
contracts rather than claims that matching production classes already exist.

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

1. [Host Runtime Contracts](runtime-contracts.md)
2. [Runner and Context](runner-and-context.md)
3. [Tool Runtime](tool-runtime.md)
4. [Delivery Runtime](delivery-runtime.md)
5. [Package Installer](package-installer.md)
6. [Scheduler](scheduler.md)
7. [Operator Gateway](operator-gateway.md)
8. [MCP Runtime](mcp-runtime.md)

## Boundary

When a runtime change affects user-visible behavior, CLI/configuration, package
recipes, runtime layout, security policy, provider behavior, state/versioning,
or test/gate workflow, update the public manual in the same change.
