---
title: Host 和 Agent Core
description: 理解 Demiurge host 和 authored Agent Core 之间的稳定 runtime 边界。
---

# Host 和 Agent Core

Demiurge 把 runtime harness 和 authored agent surface 分开。

**Host** 是稳定基础设施。它拥有 sessions、turns、provider calls、tool
execution、approvals、state、delivery、schedules、package installation、
background jobs、promotion 和 rollback。

**Agent Core** 是 authored filesystem surface。它拥有 identity、instructions、
Agent Slots、skills、tools、schedules、MCP declarations、tests 和 local
libraries。

**Agent Slot** 是 core 的可演化交互边界。它让 Core 定义的行为逻辑在受治理的位置
介入 agent loop，并组合 tools、skills、MCP、state 或其他 agents，而不需要修改 Host
harness。

## 为什么需要这个边界

自进化 agent 需要有空间改变行为，但不能让 runtime loop 变成任意的自修改系统。
Demiurge 允许 Agent Core 演化文件，但把高风险效果留在 Host-controlled capabilities
后面。

这带来三个性质：

- Agent behavior 可以作为文件阅读。
- Candidate changes 可以 diff 和 gate。
- 高风险效果仍由 Host 治理。

## Host-Owned 职责

Host 拥有：

- runtime home 解析
- source template 初始化
- core 加载与校验
- session、turn 和 step storage
- context assembly
- provider request construction
- provider calls
- tool registry and dispatch
- in-memory background job runtime
- approval and capability checks
- workspace enforcement
- external channel bridges
- scheduler claims and run logs
- package preview、install 和 uninstall
- version promotion and rollback

## Agent-Core Responsibilities

Core 拥有：

- `agent.yaml`
- `agent/SOUL.md`
- Agent Slots，目前是 bootstrap、input 和 output slots
- authored tools
- skills
- schedules
- MCP server declarations
- local libraries
- core-local tests
- 以 authored files 表达的 evolution policy

## 重要结果

Agent Core files 可以描述期望行为，但它们不拥有 provider calls、direct state
mutation、dependency installation、production promotion 或 rollback。这些仍然是 Host
functions。

Background `evolve_core` work 也遵循同一边界：它可以创建 candidate 并报告，但不会切换
active core，除非之后某个 foreground turn 要求 Host 这么做。

精确 edit 规则见
[/docs/reference/contracts/authored-surface](/docs/reference/contracts/authored-surface)。
