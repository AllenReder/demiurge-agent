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

## Background Jobs

`ToolRuntime` 不负责每个 tool 的 background state。支持后台的 tools 会把工作提交给
共享的 `JobRuntime`：

- `terminal(background=true)` 会创建一个 `terminal` backend job，并把 stdout/stderr
  捕获到 job log 中。
- `evolve_core(background=true)` 会创建一个 `evolve` backend job，并以
  `auto_promote=false` 运行；它会产出 candidate 和 report，但不会切换 active core。
- `ctx.agents.spawn(...)` 会由 runner 路由到一个 `agent` backend job。

`job` 是用于 `list`、`poll`、`log`、`wait` 和 `cancel` 的通用控制 tool。
`process` 仅作为 terminal jobs 的 compatibility view。Jobs 只存在于内存中，进程重启后
不会恢复。

每个 job 都会记录 `backend`、owner session/turn、`source_tool`、status、summary、
bounded log tail、result reference，以及可选的 `write_scope`。具有相同非空
`write_scope` 的新的 active background job 会被拒绝。

## Authored Tools

Authored tools 是 adapters。它们在发现之后，会沿用与 built-ins 相同的 host-owned
dispatch path。

## MCP Tools

MCP tools 会被命名空间隔离并过滤，以避免冲突。Transport、discovery、timeouts 和
result conversion 都由 host 负责。

## 边界

Agent Core 可以声明 tools，但它不负责 tool-call replay、authorization 或 provider
specific tool message formatting。
