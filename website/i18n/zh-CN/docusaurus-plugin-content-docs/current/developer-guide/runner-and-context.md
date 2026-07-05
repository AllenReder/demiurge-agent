---
title: Runner 和 Context
description: 面向贡献者的 turn 执行与 provider context assembly 说明。
---

# Runner 和 Context

Runner 负责 turn lifecycle。Agent Core slots 通过受控接口参与其中；它们不拥有这套
lifecycle。

## Turn Flow

```text
inbound interaction
  -> create or resume session
  -> bind inbound route to the resolved session
  -> run bootstrap when needed
  -> run input pipeline
  -> assemble provider context
  -> call provider
  -> execute tool calls through ToolRuntime
  -> continue model/tool loop until final response
  -> run output pipeline
  -> record deliveries and session events
```

## Context Layers

Provider context 可以包含：

- soul text
- skill index and loaded skills
- bootstrap output
- input module placements
- session history
- current user turn
- tool call and tool result history

Context assembler 会决定最终的 provider message 顺序和内容。

## Bootstrap

Bootstrap modules 是 session-start context producers。它们应当在 session 内保持稳定，
并且可以安全地作为参考上下文引用。

## 后台完成 turn

Runner 会为每个 session 保留一个活跃 turn。Background job completion 会被建模为
原始 session 的 synthetic inbound event，而不是直接的 channel output。Channel bridges
会在 user turn 运行时排队 completion events。如果 user input 和 completion 都在等待，
会优先运行 user input，并把待处理的 completion summaries 合并进那个 user turn。
成功的 `yield_until` 调用会在 task-worker seam 消费匹配的 pending completion
notification，因此 channel bridges 不会为同一个 task result 再运行第二个 synthetic
completion turn。

`/stop` 和 foreground cancellation 只影响当前活跃 turn。Background jobs 会继续运行，
直到完成或用户调用 `task_control(command="cancel")`。

需要用户输入的 background work 会被标记为 `blocked_needs_user`，并且不会自动批准。

## Session Delivery Routes

Runner 拥有一个共享的 `SessionInteractionRouter`。`InteractionRuntime` 会把当前
adapter 作为 `SessionRouteBinding` 传入；runner 解析出 inbound 的最终 session 后，
把该 route 绑定到 `runner.session_id`。TUI 和 channel 的 `/new`、`/resume` 以及
session switch 路径必须把同一个 adapter route 重新绑定到新的 session。

Ordinary output、tool lifecycle events 和 background output flushes 都会创建带必填
`session_id` 的 `InteractionOutbound`。Router 只投递到绑定了该 session 的 route。
如果没有 route，items 会被标记为 `unrouted`，并且不视为 adapter call failure。

## Subagent Sessions

`ctx.agents.run()`、`ctx.agents.spawn()` 和 `delegate_task` 会在独立
`session_child_*` sessions 中运行 child agents。Child runners 共享同一个 router
table，但不会接收 parent route binding。它们的 ordinary output 和 tool lifecycle
delivery 只会出现在显式绑定到 child session 的 route 上。

Parent/child lineage 仍然是 task 和 observability metadata，不参与 ordinary delivery
routing。Parent turns 通过 `AgentRunResult`、durable task completion 或未来显式
`subagent.*` events 接收 child work。

## Approval 和 Prompts

Interactive prompts 和 approval decisions 使用同一个 router 的 session-aware lookup，
但它们不是 ordinary delivery。默认情况下 approval request 按 `turn.session_id` 查找；
如果没有绑定 interactive route，approval provider 会以 `no_interactive_route` 拒绝，
除非 host、global 或 core policy 已经 auto-allow 该 action。

## Failure Handling

Slot `failure_policy` 决定失败的 slot 属于 soft 还是 hard。Provider errors、tool
errors、channel delivery errors 和 schedule errors 都由各自的 host-owned 层处理。

## 边界

不要把 provider request construction 或 session ownership 移到 Agent Core 代码里。
