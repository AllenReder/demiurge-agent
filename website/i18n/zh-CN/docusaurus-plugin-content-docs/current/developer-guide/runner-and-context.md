---
title: Runner 和 Context
description: 面向贡献者的 turn 执行与 provider context assembly 说明。
---

# Runner 和 Context

当前 alpha runner 连接各个 turn lifecycle module。`TurnAdmissionRuntime` 解析
core/session route 并启动 turn，`TurnPipelineRuntime` 运行 authored
input -> model/tool -> output 路径，`TurnPersistenceRuntime` 记录 input、assistant
output、display state、completion 与 interruption。Agent Core Slot 通过受控接口参与，
但不拥有这套 lifecycle。

该布局是冻结的 `TurnExecution`、`PrincipalScope`、不可变
`TurnExecutionContext` 与 `ContextManager` 接口的前身。权威目标契约见
[Host 运行时契约](runtime-contracts.md)。当前 runner 尚未满足下面所有不变量。

## Turn Flow

当前流程为：

```text
inbound interaction
  -> admit turn: resolve session/core, bind route, run bootstrap, begin turn
  -> run authored input pipeline
  -> assemble provider context
  -> call provider
  -> execute tool calls through ToolRuntime
  -> continue model/tool loop until final response
  -> run authored output pipeline
  -> persist input, assistant output, display state, completion, and session events
```

## 目标 TurnExecution 接口

外部 Host seam 有意保持精简：

```text
TurnExecution.run(TurnRequest) -> TurnResult
TurnExecution.cancel(TurnId, PrincipalScope) -> CancelResult
```

`TurnExecution` 必须隐藏 session admission、core-revision pinning、context preparation、
provider/tool steps、slot execution、persistence、delivery 与 cleanup。调用方提供不可变
request value，而不是 mutable runner、loaded core、store、provider client 或
capability facade。

该 module 拥有以下可观察契约：

- admission 会串行同一 session 的 turn，而不同 session 可并发运行；
- 一个 turn 捕获的 session、core revision、capability snapshot、route 与 trace identity
  不会在 await 后改变；
- provider、slot、effect、cancellation 与意外 failure 会在释放资源前创建一个
  terminal turn state；
- restart 会显式标记或恢复 orphaned admission，绝不静默重放危险的 provider/effect step；
- detached work 是独立拥有的 runtime task，而不是迟到修改已经完成的 turn。

当前 containment 实现通过 keyed admission lock 保证每个 session 在单进程内只有一个
active turn，不同 session 仍可并发。Admission 会在 bootstrap 前捕获解析出的 session；
prompt、IO、slot history/result、event、artifact 与 delivery hot path 在 await 后使用该
captured session 或不可变的 `TurnContext.session_id`。Cancellation 与 failure 会在
`finally` 中释放 admission lock。

这仍不是最终的 durable `TurnExecution` contract。Admission lock 只存在于当前进程，scope
仍携带 mutable objects，本 task 不实现 restart recovery；principal、core revision、route 与
cancellation ownership 由后续 TurnExecution/PrincipalScope 工作完成。

## Principal 与 Execution Context

`PrincipalScope` 是 Host authority，不是 Agent Core capability grant。它由已认证的
channel/operator/system facts 与持久 conversation/session bindings 派生，并为 session、
history、task、wait、cancel、resume、search 与 approval-cache 操作提供 owner predicate。

`TurnExecutionContext` 把该 principal 绑定到一个 session、turn、core revision、
capability snapshot、workspace、route token、admission lease、cancellation token 与
trace。这些绑定在该 turn 内不可变。Agent Slot 与 authored tool 继续接收精简的
author-facing SDK contexts；适用时，这些 context 会包含 `TurnContext`。它们不会获得 operator
authority、Host stores 或 admission internals。

## Context Layers

Provider context 可以包含：

- soul text
- skill index and loaded skills
- bootstrap output
- input module placements
- session history
- current user turn
- tool call and tool result history

当前 `ContextAssembler` 决定最终 provider message 的顺序和内容。它不知道 model
context window，不会预留 output budget，也不会触发 automatic compaction。

目标 `ContextManager.prepare()` 在 provider I/O 前负责 layer budgets、完整 request
估算、低成本 pruning、compaction lease 与 fallback，以及 typed overflow。
`ContextManager.observe()` 消费 normalized usage 与 finish-reason observations，而不依赖
环境中的 mutable session state。在该 module 实现之前，manual `/compact` 仍是当前
alpha 机制。

## Bootstrap

Bootstrap modules 是 session-start context producers。它们应当在 session 内保持稳定，
并且可以安全地作为参考上下文引用。

## 后台 Task 完成 Turn

Background task completion 会被建模为原始 session 的 synthetic inbound event，而不是
直接的 channel output。Channel bridge 使用 live subscription 作为 wakeup path，并从
SQLite 恢复 pending completion events。如果 user input 和 completion 同时等待，会先运行
user input，并把 pending completion summaries 合并进该 user turn。Completion notification
使用 durable work state：bridge 在排队或合并 synthetic inbound 前先 claim `ready` work，
并且只通过 task-worker seam acknowledge。成功的 `yield_until` 调用会 claim 并 acknowledge
匹配的 pending completion，因此 channel bridge 不会为同一 task result 再运行第二个
synthetic completion turn。

Parallel input 与 output slots 仍会并发调度，但 runner 会等待其 Host-managed work 完成，
再把 parent turn 标成 terminal。Detached slot work 必须建模为 child runtime task，不能在
parent turn 完成后继续修改它。这里的 **parallel** 表示在 parent turn 内并发但会等待
汇合；它既不是 detached，也不具备 restart durability。

`/stop` 与 foreground cancellation 只影响 active turn。Background task 会继续运行，
直到完成或用户调用 `task_control(command="cancel")`。

需要用户输入的 background work 会标成 `blocked_needs_user`，且不会自动批准。

## Session Delivery Routes

Runner 拥有共享的 `SessionInteractionRouter`。`InteractionRuntime` 把当前 adapter 作为
`SessionRouteBinding` 传入；runner 解析 inbound 的最终 session 后，会把该 route 绑定到
admission 捕获的 session id。TUI 和 channel 的 `/new`、`/resume` 与 session switch 路径必须把
同一个 adapter route 重新绑定到新 session。

External channel conversation 还有一层 durable binding，key 为
`(core_id, channel, conversation_key)`。`conversation_key` 是根据明确 platform facts
构造的 canonical Host-owned route key，例如 `telegram:dm:123` 或
`slack:channel:T1:C1:thread:123.4`。Channel `/resume` 会把当前 conversation key 重新
绑定到 resumed session，因此同一 external conversation 的下一条 inbound message 会继续
进入同一 transcript。

Containment path 现在会用 captured turn session 构造 delivery，不再重新读取
`runner.session_id`。最终 contract 仍需把 route token 本身放进 `TurnExecutionContext`，让
restart、owner check 与 route lifetime 由同一个 durable execution interface 表达。

Ordinary output、tool lifecycle events 与 background output flushes 会创建带必填
`session_id` 的 `InteractionOutbound`。Router 只向绑定该 session 的 route 投递。如果
没有 route，item 会标成 `unrouted`，且不视为 adapter call failure。

## Subagent Sessions

`ctx.agents.run()`、`ctx.agents.spawn()` 与 `delegate_task` 会在独立的
`session_child_*` session 中运行 child agent。Child runner 共用同一个 router table，
但不会接收 parent route binding。它们的 ordinary output 与 tool lifecycle delivery
只会出现在显式绑定到 child session 的 route 上。

Parent/child lineage 仍是 task 与 observability metadata，不参与 ordinary delivery
routing。Parent turn 通过 `AgentRunResult`、durable task completion 或未来显式的
`subagent.*` events 接收 child work。

## Approval 与 Prompts

Interactive prompt 与 approval decision 使用同一 router 的 session-aware lookup，但它们
不是 ordinary delivery。默认按 `turn.session_id` 查找 approval request；如果没有绑定
interactive route，approval provider 会以 `no_interactive_route` 拒绝，除非 Host、
global 或 core policy 已经 auto-allow 该 action。

当前 session-allow cache 尚未按 principal/session 划分 scope。目标 cache key 与每次
lookup 归 `ApprovalRuntime` 管理，并消费不可变 `PrincipalScope`；仅有 route lookup 不等于
authorization。

## Failure Handling

在当前 alpha runner 中，Slot `failure_policy` 决定失败的 slot 属于 soft 还是 hard。
受保护的 input、provider/model-loop 与 output stage 内发生的 exception/cancellation，
会在重新抛出前写入 terminal turn state。Tool-catalog preparation 当前位于这些受保护
region 之外，因此其 failure 尚无相同保证。Foreground turn 不是 `RuntimeTask`；channel
delivery、background task 与 schedule error 仍由各自 Host module 所有。

目标 `TurnExecution` 接口会返回 typed failed/cancelled product outcome，并且只暴露 typed
rejection 或 infrastructure failure。Adapter exception 不会成为其 caller interface 的一部分。

## 边界

不要把 provider request construction、context budgeting、principal authority 或 session
ownership 移到 Agent Core 代码中。
