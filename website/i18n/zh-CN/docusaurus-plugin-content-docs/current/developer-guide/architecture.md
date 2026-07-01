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
        +--> JobRuntime
        +--> Delivery/session stores
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
| Job runtime | 跟踪内存中的 background jobs、logs、write scopes 和 completion events。 |
| Delivery runtime | 将 authored delivery requests 转换为 session records、events、artifacts 和 channel output。 |
| Scheduler | 领取到期 schedules 并运行新的 sessions。 |
| Package manager | 预览、安装、卸载并记录 package repository components。 |

## 入口点

- `demiurge/cli.py`
- `demiurge/app/__init__.py`
- `demiurge/runtime/runner.py`
- `demiurge/tools/runtime.py`
- `demiurge/jobs.py`
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
