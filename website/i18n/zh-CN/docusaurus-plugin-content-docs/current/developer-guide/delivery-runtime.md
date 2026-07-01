---
title: 交付运行时
description: 面向贡献者的 session records、live output、artifacts 和 channels 说明。
---

# 交付运行时

Delivery runtime 会把输出请求转换为持久化的 session records、实时
events、artifacts 和 channel items。

## 来源

Delivery requests 可以来自：

- output modules
- authored tools
- schedule runs
- channel bridge logic

## 历史策略

持久化的 delivery 会成为持久的 assistant history。临时 delivery 适合进度、
notice 和仅实时输出。

## Artifacts

Artifacts 由 host-owned records 表示。Output modules 可以请求 artifact delivery，
但路径、metadata 和持久化都由 host 负责。

## Channels

Channel bridges 会把 delivery 适配为平台特定消息。它们也会携带用于 scheduled
和 asynchronous delivery 的 route context。

## 边界

不要让 output modules 直接写 session history 或 channel state。
