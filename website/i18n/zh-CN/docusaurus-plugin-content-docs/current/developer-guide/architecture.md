---
title: 架构
description: 为贡献者梳理当前 Demiurge Host 运行时。
---

# 架构

本指南同时描述当前 alpha 实现，以及后续加固工作必须收敛到的 Host 运行时接口。
它不是稳定的插件 API。

## 系统概览

当前实现以 runner 为中心：

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

冻结的目标 ownership 以 module 为中心：

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

`PrincipalScope` 与不可变的 `TurnExecutionContext` 在这些 Host 接口之间携带 authority
和 turn identity。Agent Core 作者仍使用更小的 `ctx.*` SDK 与 Agent Slot 接口，不会
直接调用这些 Host 接口。

## 当前与目标契约

完整的接口、顺序、错误、取消、重启、性能与 finding-owner 规则冻结在
[Host 运行时契约](runtime-contracts.md)中。尤其是在增加另一个 policy 或 lifecycle
owner 前，请先查看其中的
[primary finding owner map](runtime-contracts.md#primary-finding-owners)。

| 契约 | 当前 alpha 实现 | 冻结目标 |
| --- | --- | --- |
| `PrincipalScope` | 不可变 Host-derived authority 现已治理 session list/resume/search、task detail/wait/cancel、`/subagents`、durable session/message/task query，以及 approval cache ownership/invalidation。 | 一个不可变 owner predicate 覆盖每个 session、task、approval、history 与 effect 操作；后续 EffectRuntime 工作把同一 seam 应用到每个 effect adapter。 |
| `TurnExecutionContext` | Frozen turn identity 已固定 principal、session、revision、capability declarations、route、cancellation、admission 与 trace；mutable lifecycle/state handle 仍是内部对象。 | 深度不可变的 principal、session、revision、capability、route、admission、cancellation 与 trace bindings。 |
| `TurnExecution` | `run(TurnRequest)` 拥有 admission 到 completion；owner-checked `cancel(...)`、captured-route delivery、并发与 cleanup 已实现。 | Typed outcome、durable admission/restart recovery 与最终 immutable result projection 完整闭合 lifecycle。 |
| `EffectRuntime` | `ToolRuntime`、`McpRuntime`、security helpers 与内联 file/process/network 代码分别拥有 dispatch 的不同部分。 | Builtin、authored 与 MCP effect 使用同一个 resolved effect entry 和同一套 policy/dispatch 顺序。 |
| `ContextManager` | Layer assembly 与 manual compaction 相互分离；没有自动 model-window budget。 | `prepare()` 与 `observe()` 拥有 budgeting、pruning、compaction、usage calibration 与 overflow semantics。 |
| `ChannelInbox` | Platform adapter 把 inbound event 直接传给 runner；没有共享的 durable inbound owner。 | 一个接口后统一 durable accept、dedup、cursor、claim、complete/fail、retry 与 DLQ 语义。 |

`EffectRuntime`、`ContextManager` 与 `ChannelInbox` 仍是目标贡献者契约，并不表示匹配的
production class 已经存在。

## 主要子系统

| 子系统 | 职责 |
| --- | --- |
| CLI | 解析命令，并启动 TUI、gateway、setup、package、update 与 doctor 流程。 |
| App factory | 解析 runtime home、config、source templates、core、workspace、provider、approvals 与 tools。 |
| Core loader | 加载 `agent.yaml`、slots、pipelines、skills、schedules 与 MCP declarations。 |
| Runner | 连接 Host 运行时 module，负责 turn admission、authored pipeline execution、persistence、tools、slots 与 delivery。 |
| Context assembler | 根据 soul、skills、bootstrap、input、history 与 current turn 构建 provider messages。 |
| Tool runtime | 构建可见 registry，并执行 built-in、authored 与 MCP tools。 |
| Core repository | 拥有 Git-backed runtime agents tree、refs、change sets、package transactions 与 rollback commits。 |
| Evolution runtime | 通过 core repository 与 gates 启动、审查、promote 和 discard 隔离的 agents-tree change sets。 |
| Gate runner | 运行 Host-owned checks：path safety、artifacts、dependency files、core loading、package provenance、drift warnings 与 cross-core references。 |
| Runtime control plane | 提交 detached task specs，投影 task events，并从 SQLite 暴露 task/session/scheduler/outbox state。 |
| Host work lifecycle | 跨 durable work items、detached tasks、delivery、schedules 与 completion inboxes，claim、complete、fail、cancel、acknowledge 和 observe Host-managed work。 |
| Operator gateway | 拥有本地 TUI/dashboard product state、operator events、prompts、approvals、status、history 与 slash-command routing。 |
| Runtime task worker | 持有 active process handles 与 live completion subscribers；pending completions 从 SQLite events 恢复。 |
| Delivery runtime | Dispatch authored delivery intents 并更新 outbox status。 |
| Session runtime | 读写 session、turn、message、bootstrap 与 compaction projections。 |
| Scheduler | 通过 SQLite scheduler projections 领取到期 schedules 并运行新的 sessions。 |
| Package manager | 预览、安装、卸载并记录 package repository components。 |

## 入口点

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

## 阅读顺序

1. [Host 运行时契约](runtime-contracts.md)
2. [Runner 与 Context](runner-and-context.md)
3. [工具运行时](tool-runtime.md)
4. [Delivery 运行时](delivery-runtime.md)
5. [Package Installer](package-installer.md)
6. [Scheduler](scheduler.md)
7. [Operator Gateway](/docs/developer-guide/operator-gateway)
8. [MCP 运行时](mcp-runtime.md)

## 边界

当运行时变更影响用户可见行为、CLI/configuration、package recipes、runtime layout、
security policy、provider behavior、state/versioning 或 test/gate workflow 时，需要在
同一次改动中更新公开手册。
