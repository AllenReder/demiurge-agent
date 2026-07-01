---
title: 调度器
description: 面向贡献者的 host-owned schedule claims 和 runs 说明。
---

# 调度器

Schedules 由 Agent Cores 声明，并由 host 执行。

## Runtime Files

Core-authored schedules 位于：

```text
agent/schedules/*.yaml
```

Host scheduler state 位于：

```text
~/.demiurge/scheduler/<core_id>/
```

## Claim Flow

Scheduler 会根据 cron expressions 和 runtime timezone 计算到期时间。某个 schedule
到期时，host 会记录 claim 并推进下一次运行时间。

## Run Flow

每次运行都会创建一个带 synthetic inbound metadata 的全新 scheduled session。Runner
会使用该 schedule 选择的 input 和 output modules 执行 schedule prompt。

## Delivery

本地 delivery 会保留在本地 session records 中。External delivery 会在发送前校验已
配置的 channel 和 target。

## 边界

Agent Core 负责声明 schedules。Host 负责 durable job state、claims、run records、
session creation 和 channel delivery。
