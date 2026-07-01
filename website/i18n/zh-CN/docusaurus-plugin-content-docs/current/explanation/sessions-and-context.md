---
title: 会话与上下文
description: 理解 turns 如何变成 provider context 和持久化 session 记录。
---

# 会话与上下文

Demiurge 的一次 run 由 session、turn 和 step 组成。

- **session** 是持久化的会话容器。
- **turn** 是一次来自用户、channel、schedule 或 tool-triggered action 的输入动作。
- **step** 是 turn 内部 model/tool loop 的一部分。

这个结构由 host 拥有。

## 上下文组装

Provider context 由多个来源组装而成：

1. core soul 和 runtime instructions
2. skill index 和已加载的 skills
3. bootstrap context
4. input module placements
5. 按 history policy 处理的 session history
6. 当前 turn 的输入

Input modules 可以添加当前 turn 的内容，但最终的 provider message 形状由 host
决定。

## 引导上下文

Bootstrap modules 在 session 开始时运行，可以提供稳定的上下文，例如 memory notes
或连续性摘要。它们应该把已保存的事实当作参考材料，不要把过期上下文伪装成权威。

## 历史策略

Output modules 和 delivery calls 可以选择 delivered content 是否写入 session
history。被持久化的内容之后可能再次出现在上下文中。Transient notices 和 progress
messages 可以 live delivery，而不会变成 durable assistant history 的一部分。

## 恢复与压缩

Session 可以通过 id 恢复。只要当前路径的 runtime 支持，手动 compaction 可以压缩长
session。持久化 session 存储，而不是 Agent Core，拥有 history persistence。

## 边界

Agent Cores 可以塑造 inputs 和 outputs。它们不拥有 session storage、history
replay rules、provider message construction 或 context-budget policy。
