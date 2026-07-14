---
title: 会话与上下文
description: 理解 turn 如何成为 provider context 与持久 session record。
---

# 会话与上下文

一次 Demiurge 运行由 session、turn 与 step 组织：

- **session** 是持久的 conversation container；
- **turn** 是一次由 user、channel、schedule 或 tool 触发的 inbound action；
- **step** 是 turn 内 model/tool loop 的一部分。

该结构由 Host 拥有。

## 上下文组装

Provider context 由多个来源组装：

1. core soul 与 runtime instructions
2. skill index 与 loaded skills
3. bootstrap context
4. input module placements
5. 根据 history policy 选择的 session history
6. current turn input

Input module 可以添加 current-turn content，但最终 provider message shape 由 Host 决定。

## 引导上下文

Bootstrap module 在 session 启动时运行，并可提供 memory note 或 continuity summary 等稳定
context。它们应把存储的事实视为参考材料，避免假装 stale context 具有权威性。

## 历史策略

Output module 与 delivery call 可以决定是否把 delivered content 持久化到 session history。
持久化内容可出现在后续 context 中。Transient notice 与 progress message 可以实时投递，
而不进入持久 assistant history。

## 恢复与压缩

Session 可以按 id 恢复。当当前路径的运行时支持时，manual compaction 可以总结过长的
session。History persistence 归 durable session store 而不是 Agent Core 所有。

当前 alpha 运行时不会自动根据 model context window 制定预算、预留 output token、在
overflow 前压缩，也不会串行 concurrent compaction。冻结目标
`ContextManager.prepare()/observe()` 拥有这些规则；`PrincipalScope` 与
`TurnExecutionContext` 则保证 history 与 resume 操作始终绑定到已认证的 session owner。
参见 [Host 运行时契约](../developer-guide/runtime-contracts.md)。

## 边界

Agent Core 可以调整 input 与 output，但不拥有 session storage、history replay rules、
provider message construction 或 context-budget policy。
