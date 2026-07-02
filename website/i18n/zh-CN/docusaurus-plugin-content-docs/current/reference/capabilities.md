---
title: Capabilities 和 Approvals
description: Host-owned capability 和 approval behavior 的参考。
---

# Capabilities 和 Approvals

Capabilities 是 host-owned grants，用于 effect classes。Approval policy 决定某个 requested effect 是可以自动运行、必须提示，还是被拒绝。

仅声明 tool、slot、schedule 或 MCP server，并不足以运行危险 effects。Host 会在执行时检查 capabilities。

## Capability Grants

Capabilities 可以全局授予给 core：

```yaml
capabilities:
  defaults:
    fs.read:
      scope: workspace
    terminal.exec:
      scope: workspace
```

也可以授予给某个 authored component path：

```yaml
capabilities:
  slots:
    agent/output/archive_summary:
      fs.write:
        scope: workspace
```

Slot 和 authored-tool manifests 也可以声明本地 `capabilities` 列表：

```yaml
capabilities:
  - fs.read
  - tool.call:project_note
```

运行时，authored code 调用：

```python
ctx.capability.require("fs.read", slot_path=ctx.slot_path)
```

如果未声明该 capability，host 会抛出 `capability denied`。

## Prefix Grants

Capability checker 支持精确 keys 和 prefix wildcards：

```yaml
capabilities:
  defaults:
    mcp.call:*:
      scope: core
```

这会授予 `mcp.call:docs` 等 capabilities。

## 常见 Capabilities

| Capability | Meaning |
| --- | --- |
| `fs.read` | 通过 host checks 或需要它的 authored component 读取 workspace files。 |
| `fs.write` | 写入 workspace files。 |
| `terminal.exec` | 在 workspace scope 中运行 terminal commands。 |
| `network.fetch` | 获取 network content。 |
| `schedule.manage` | 管理 core schedule YAML files。 |
| `task.control` | 列出、检查、等待或取消 background runtime tasks。 |
| `tool.call:<tool>` | 允许 authored code 通过 `ctx.tools.call(...)` 调用 visible tool。 |
| `mcp.call:<server>` | 允许模型调用某个 MCP server 上的 tools。 |
| `skill.activate` | 允许 input slots 激活 skills。 |
| `skill.activate:<skill>` | 允许 input slots 激活特定 skill。 |
| `state.read` | 通过 `ctx.state` 读取 host state。 |
| `state.write` | 通过 `ctx.state` 写入 host state。 |
| `state.propose` | 提交 legacy state proposal effects。 |
| `agents.run:<core>` | 同步运行 child agent。 |
| `agents.spawn:<core>` | 生成 child agent task。 |
| `tool.call:evolve_core` | 通过 host 创建并 promote candidate core。 |
| `tool.call:rollback_core` | 通过 host 回滚 active core pointer。 |

## Approval Policy

Approval policy values 是：

```text
auto < prompt < deny
```

Risk values 是：

```text
low < medium < high < critical
```

对于大多数 tools，host 会从 tool metadata 开始，然后应用 core approval overrides，再应用 global fallback approval。更严格的 core policy 会优先于 tool metadata。Global fallback approval 是 host-level policy，可作为最终 default。

## Core Approval Config

```yaml
approval:
  default: null
  tools:
    terminal: prompt
  capabilities:
    fs.write: prompt
  risks:
    critical: deny
```

`tools` 匹配 tool names。`capabilities` 匹配 request 使用的 capability。`risks` 匹配 request risk。

## Tool Registry Metadata

`tools.metadata` 会改变 registry metadata：

```yaml
tools:
  metadata:
    web_extract:
      approval_policy: deny
      risk: medium
      capability: network.fetch
```

Built-in tools 不能通过 core metadata 变得更宽松。Authored 和 MCP tools 可以被完全覆盖，因为它们是 core-declared surfaces。

## 边界

Capabilities 本身不是 sandbox。Effects 执行前，host 仍会强制执行 workspace scope、sensitive path checks、command guards、approval prompts、channel policy 和 tool runtime rules。
