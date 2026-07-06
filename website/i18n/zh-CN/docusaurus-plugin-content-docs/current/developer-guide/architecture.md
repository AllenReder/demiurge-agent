---
title: 架构
description: 为贡献者梳理当前 Demiurge host runtime。
---

# 架构

本指南描述当前实现。它不是稳定的插件 API。

## 系统概览

```text
CLI / TUI / Gateway
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
        +--> RuntimeTaskWorker
        +--> DeliveryRuntime
        +--> SessionRuntime
        +--> SchedulerService
```

## 主要子系统

| 子系统 | 职责 |
| --- | --- |
| CLI | 解析命令，并启动 TUI、gateway、setup、package、update 和 doctor 流程。 |
| App factory | 解析 runtime home、config、source templates、core、workspace、provider、approvals 和 tools。 |
| Core loader | 加载 `agent.yaml`、slots、pipelines、skills、schedules 和 MCP declarations。 |
| Runner | 负责 session、turn、step、bootstrap、input、model/tool loop、output 和 delivery flow。 |
| Context assembler | 根据 soul、skills、bootstrap、input、history 和 current turn 构建 provider messages。 |
| Tool runtime | 构建可见 registry，并执行 built-in、authored 和 MCP tools。 |
| Core repository | 拥有 Git-backed runtime agents tree、refs、change sets、package transactions 和 rollback commits。 |
| Evolution runtime | 通过 core repository 和 gates 管理 isolated agents-tree change sets 的 start、review、promote 和 discard。 |
| Gate runner | 运行 host-owned checks：path safety、artifacts、dependency files、core loading、package provenance、drift warnings 和 cross-core references。 |
| Runtime control plane | 提交 detached task specs，投影 task events，并从 SQLite 暴露 task/session/scheduler/outbox state。 |
| Runtime task worker | 持有 active process handles 和 live completion subscribers；pending completions 从 SQLite events 恢复。 |
| Delivery runtime | Dispatch authored delivery intents 并更新 outbox status。 |
| Session runtime | 读写 session、turn、message、bootstrap 和 compaction projections。 |
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
- `demiurge/runtime/store.py`
- `demiurge/runtime/tasks.py`
- `demiurge/runtime/outbox.py`
- `demiurge/tools/runtime.py`
- `demiurge/channels/gateway.py`
- `demiurge/packages.py`
- `demiurge/scheduler/__init__.py`

## 阅读顺序

1. [runner-and-context.md](runner-and-context.md)
2. [tool-runtime.md](tool-runtime.md)
3. [delivery-runtime.md](delivery-runtime.md)
4. [package-installer.md](package-installer.md)
5. [scheduler.md](scheduler.md)
6. [mcp-runtime.md](mcp-runtime.md)

## 边界

当 runtime 变更影响用户可见行为、CLI/configuration、package recipes、runtime layout、security policy、provider behavior、state/versioning 或 test/gate workflow 时，需要在同一次改动里更新公开手册。
