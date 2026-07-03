---
title: 工具运行时
description: 面向贡献者的 tool discovery、metadata、dispatch、approvals 和 results 说明。
---

# 工具运行时

Tool runtime 会构建可见的 tool registry 并执行调用。

## Registry Sources

Tools 可以来自：

- built-in toolsets
- `agent/tools/` 下的 authored tools
- 从 `agent/mcp/*.yaml` 发现的 MCP tools

`agent.yaml` 会选择 built-in toolsets，并可以覆盖 tool metadata。

## Dispatch

运行时会：

1. resolve tool registry entry
2. check enabled state
3. apply capability and approval policy
4. 在相关情况下强制执行 workspace 和 safety rules
5. 执行 built-in、authored 或 MCP tool
6. 将结果转换为 model history 和 user display 所需格式

## Background Tasks

`ToolRuntime` 不负责每个 tool 的 background state。支持后台的 tools 会把工作提交给
host runtime，并使用共享的 `RuntimeTaskWorker` 作为 active work 的 live worker：

- `terminal(background=true)` 会创建一个 `terminal.exec` task，并把 stdout/stderr
  捕获到 `task_logs` 中。
- `run_terminal(...)` 是 model-facing alias，默认使用 `background=true`。
- `evolve_core(action="start", background=true)` 会创建一个 `evolver.run` task，编辑
  隔离 agents-tree worktree。它返回 run id，不会切换 live core。
- `evolve_core(action="review")`、`evolve_core(action="promote")` 和
  `evolve_core(action="discard")` 通过 host-owned evolution runtime 操作该 run id。
  Promotion 只有在 gates 通过且 high-risk tool call 被批准后才会推进 Git refs。
- `ctx.agents.spawn(...)` 会由 runner 路由到一个 `agent.spawn` task。
- `delegate_task(...)` 由 active runner context 执行，并创建一个 `agent.spawn` task。

`task_list`、`task_status`、`task_control` 和 `yield_until` 是 model-facing
runtime-task controls。`task_control` 目前只支持 `command="cancel"`。

每个 background task 都会记录 `kind`、owner session/turn、`source_tool`、status、
summary、bounded log tail、result reference，以及可选的 `write_scope`。具有相同非空
`write_scope` 的新的 active background task 会被拒绝。

## Authored Tools

Authored tools 是 adapters。它们在发现之后，会沿用与 built-ins 相同的 host-owned
dispatch path。

## MCP Tools

MCP tools 会被命名空间隔离并过滤，以避免冲突。Transport、discovery、timeouts 和
result conversion 都由 host 负责。

## 边界

Agent Core 可以声明 tools，但它不负责 tool-call replay、authorization 或 provider
specific tool message formatting。
