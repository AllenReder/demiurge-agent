---
title: 交付运行时
description: 面向贡献者的 session records、live output、artifacts 和 channels 说明。
---

# 交付运行时

Delivery runtime 会把输出请求转换为持久化的 session records、实时
events、artifacts 和 channel items。

每次 output `send_*` 调用也会向 SQLite runtime `outbox` projection 写入一个
delivery intent，以及匹配的 `delivery.send` durable work item。`DeliveryRuntime`
通过 `SessionInteractionRouter` dispatch；router 只按 `InteractionOutbound.session_id`
查找当前活跃 route。Channel adapters 绑定到 session，并把 payload 适配为平台 API，
但不拥有 durable delivery state。

内存中的 `InteractionItem.dispatch_status` 生命周期是
`pending -> scheduled -> delivered/failed/unrouted`。持久化 outbox 生命周期是
`queued -> sending -> sent/failed/unknown/unrouted`。`unrouted` 表示该 outbound
session 当前没有绑定 route；它不同于 `failed`，后者表示 route 存在但 adapter
delivery 抛错。

## 来源

Delivery requests 可以来自：

- output modules
- authored tools
- schedule runs
- channel adapter logic

## 历史策略

持久化的 delivery 会成为持久的 assistant history。临时 delivery 适合进度、
notice 和仅实时输出。

## Artifacts

Artifacts 由 host-owned records 表示。Output modules 可以请求 artifact delivery，
但路径、metadata 和持久化都由 host 负责。

## Session Routes

`InteractionOutbound.session_id` 是必填字段。`channel` 字段只是 adapter metadata，
不再决定 route ownership。普通 live delivery 只按 `outbound.session_id` 路由。

`SessionInteractionRouter` 拥有 live route table：

- `bind(session_id, route)` 把 TUI、Telegram 或其他 adapter route 绑定到一个
  session，并返回 token。
- `unbind(token)` 移除这个 live route。
- `deliver(outbound)` 只发送到 `outbound.session_id` 绑定的 route。
- `prompt_user(prompt)` 和 `request_approval(request)` 使用单独的 session-aware
  route lookup 处理交互 prompt 和 approval。

Route 会防御性拒绝任何非自身绑定 session 的 outbound payload。
`InteractionRuntime.handle()` 会在 runner 解析出最终 session 后绑定 inbound route。
`/new`、`/resume` 和 session switch 路径必须重新绑定到新的 session。

## Channels

Channel adapters 会把 delivery 适配为平台特定消息。它们不再作为嵌套 work
继承的 ambient bridge。若某个 session 没有 active route，普通 delivery 和 tool
lifecycle item 会被标记为 `unrouted`，不会作为 `InteractionRuntime.handle()` 的
pending fallback output 返回。

如果 bridge delivery 在 history 已写入后失败，history row 会保持 durable。
Non-text delivery 使用 `write_history=True` 时必须提供显式 `history_text`；host
不会发明 artifact placeholder text。可选的 `failure_history_text` 可以在首次失败时
替换 history row。后续 retry status update 不能重写该 body。

Host 会在平台 I/O 开始前 claim delivery。若进程在 `sending` 之后、平台结果持久化
之前崩溃，recovery 会把该 delivery 标记为 `unknown`，而不是自动重放。能与平台状态
对账的 channel 可以解析 `unknown`；否则它保持为 operator-visible state。

## Subagents

Child agent runs 使用独立的 `session_child_*` sessions。Router 不知道 parent/child
lineage。Child ordinary output 和 tool lifecycle delivery 只进入显式绑定到 child
session 的 route。没有这样的 route 时，它们是 `unrouted`；parent 只能通过显式
`AgentRunResult`、task completion 或未来 observability events 看到 child。

## 边界

不要让 output modules 直接写 session history 或 channel state。
不要按 parent/child relationship、conversation key 或 ambient adapter state 路由
delivery。
