---
title: Capability Boundary 合约
description: Effects、approvals、workspace scope 和 host-owned controls 的稳定规则。
---

# Capability Boundary 合约

Demiurge capabilities 由 host 拥有。Authored files 可以请求 effects；host 决定这些 effects 是否运行。

## Host-Mediated Effects

这些 effects 必须通过 host-owned interfaces 或显式 host capability checks：

- filesystem reads and writes
- terminal execution
- network fetches
- provider calls
- tool execution
- MCP tool calls
- schedule management
- state reads and writes
- child agent runs and spawns
- version promotion
- rollback
- package repository trust
- dependency changes

## Capability Rule

Authored code 必须 require 它依赖的 capability：

```python
ctx.capability.require("fs.read", slot_path=ctx.slot_path)
```

Capability 必须在以下任一位置声明：

- `agent.yaml` 下的 `capabilities.defaults`
- `agent.yaml` 下的 `capabilities.slots.<slot_path>`
- component manifest 的 `capabilities` 列表

`mcp.call:*` 这样的 prefix grants 可以授予 `mcp.call:docs` 等 scoped capabilities。

## Approval Rule

Approval policy 可以来自：

- built-in tool metadata
- authored tool metadata
- MCP server metadata
- `tools.metadata`
- `agent.yaml` approval config
- global fallback approval config
- channel/runtime approval provider behavior

`deny` 始终是终止性结果。`prompt` 需要 approval provider。只有在 capability 和 workspace checks 通过之后，`auto` 才能不询问就运行。

## Workspace Rule

File 和 terminal operations 必须留在 resolved workspace 内，除非 host 显式允许另一个 root。Authored code 不应硬编码私有本地路径。

## Secrets Rule

Provider keys、bot tokens、webhook secrets、SMTP credentials 和 MCP secrets 属于 host config、environment variables 或 `.env`。Authored slots 和 tools 应报告 secret sources，而不是 secret values。

## Channel Rule

External channels 在创建 turn 之前验证 inbound events。例如：

- Telegram 检查 `allowed_users` 和 `allowed_chats`。
- Webhook 检查 token 或 `allow_unauthenticated`。
- Slack 检查 request signatures。
- Mattermost 检查 webhook tokens。
- Matrix 检查 homeserver credentials 和可选 room allowlists。
- Email 检查 credentials 和可选 sender/recipient allowlists。

Agent Core 不会因为启用了 channel 就获得 network authority。

## Dependency Rule

Candidate Agent Cores 和 package recipes 不得安装 Python dependencies。请把 dependency needs 记录为 manual review items。

## 边界

Capability declaration 是 effect 的必要条件，但不是完整 security decision。Host 仍会应用 workspace scope、command guards、approval policy、channel policy、runtime task rules 和 provider/tool runtime rules。
