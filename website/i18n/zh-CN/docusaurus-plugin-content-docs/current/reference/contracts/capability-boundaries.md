---
title: Capability Boundary 规则
description: 效果、approval、workspace scope 和 host-owned controls 的稳定规则。
---

# Capability Boundary 规则

Demiurge capabilities 是 host-owned 的。authored files 可以请求 effects；是否运行由 host 决定。

## Host-Owned Effects

这些 effects 必须经过 host-owned interfaces：

- filesystem reads and writes
- terminal execution
- network fetches
- provider calls
- tool execution
- schedule management
- production state mutation
- version promotion
- rollback
- package repository trust
- dependency changes

## Workspace Rule

文件和 terminal operations 必须留在 resolved workspace 内，除非 host 明确允许其它范围。slot 不应硬编码私有本地路径。

## Approval Rule

approval policy 可以来自 built-in tool metadata、`agent.yaml` overrides、risk policy、capability policy 或 channel/runtime policy。限制更强的 policy 胜出。

## Secrets Rule

slots 和 tools 不应打印 secrets。provider secrets 应放在 host config、environment variables 或 `.env` 中。status output 应显示来源，而不是值。

## Channel Rule

external channels 在创建 turn 之前必须验证 inbound requests。Telegram allowlists、webhook tokens、Slack signatures 以及类似检查都属于 channel bridge 的职责。

## Dependency Rule

Candidate Agent Cores 和 package recipes 不能安装 Python dependencies。依赖需求应记录为 manual review items。
