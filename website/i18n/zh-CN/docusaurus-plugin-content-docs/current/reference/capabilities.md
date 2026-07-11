---
title: Capabilities 和 Approvals
description: Host-owned capability 与 approval 行为参考。
---

# Capabilities 和 Approvals

Capability 是 Host-owned 的 effect class grant。Approval policy 决定请求的 effect 能否
自动运行、必须 prompt，或被拒绝。

声明 tool、slot、schedule 或 MCP server 本身不会授予 capability。Builtin 与 MCP call
handler 通常会在执行时检查所需 capability。Authored tool dispatch 会在 module import
前要求 singular registry capability；module 内 authored SDK client 仍会强制执行显式
`ctx.capability.require(...)` 调用。

当前 alpha 限制：MCP spawn/connect/discovery 会发生在之后的 call capability 与 approval
check 之前。Core mutation builtins 现在会在每个 evolution/version-store adapter call 或
background task 创建前，要求 resolved registry capability 并执行 effective approval policy。
统一的目标顺序见
[Host 运行时契约](../developer-guide/runtime-contracts.md#effectruntime)。Capability 既不是
principal authorization，也不是 Python sandbox。

## Capability Grants

Capability 可以全局授予 core：

```yaml
capabilities:
  defaults:
    fs.read:
      scope: workspace
    terminal.exec:
      scope: workspace
```

也可以授予某个 authored component path：

```yaml
capabilities:
  slots:
    agent/output/archive_summary:
      fs.write:
        scope: workspace
```

Slot 与 authored-tool manifest 也可以声明本地 `capabilities` list：

```yaml
capabilities:
  - fs.read
  - tool.call:project_note
```

运行时 authored code 调用：

```python
ctx.capability.require("fs.read", slot_path=ctx.slot_path)
```

如果未声明 capability，Host 会抛出 `capability denied`。
这个 plural implementation grant 不会满足 authored tool 的 singular registry
`capability`；pre-import dispatcher gate 需要 core default 或 path-scoped capability grant。

## Prefix Grants

Capability checker 支持精确 key 与 prefix wildcard：

```yaml
capabilities:
  defaults:
    mcp.call:*:
      scope: core
```

这会授予 `mcp.call:docs` 等 capabilities。

## 常见 Capabilities

| Capability | 含义 |
| --- | --- |
| `fs.read` | 通过 Host check 或要求该 capability 的 authored component 读取 Host-visible files。Workspace 外与 sensitive read 需要 approval。 |
| `fs.write` | 写入 workspace files。 |
| `terminal.exec` | 在 workspace scope 运行 terminal command。 |
| `network.fetch` | 获取 network content。 |
| `schedule.manage` | 管理 core schedule YAML files。 |
| `task.control` | 列出、检查、等待或取消 background runtime tasks。 |
| `tool.call:<tool>` | 允许 authored code 通过 `ctx.tools.call(...)` 调用可见 tool。 |
| `mcp.call:<server>` | 允许 model 调用 MCP server 的 tools。 |
| `skill.activate` | 允许 input slot 激活 skills。 |
| `skill.activate:<skill>` | 允许 input slot 激活指定 skill。 |
| `state.core.read` | 通过 `ctx.state.core` 读取 core-scoped Host state。 |
| `state.core.write` | 通过 `ctx.state.core` 写入 core-scoped Host state。 |
| `state.session.read` | 通过 `ctx.state.session` 读取 session-scoped Host state。 |
| `state.session.write` | 通过 `ctx.state.session` 写入 session-scoped Host state。 |
| `agents.run:<core>` | 同步运行 child agent。 |
| `agents.spawn:<core>` | Spawn child agent task。 |
| `tool.call:evolve_core` | 启动、审查、promote 或 discard Host-owned evolve run。 |
| `tool.call:rollback_core` | 为 live Agent Core tree 创建 rollback commit。 |

## Approval Policy

Approval policy values：

```text
auto < prompt < deny
```

Risk values：

```text
low < medium < high < critical
```

对于 authored tool 与 core mutation builtin，Host 从 tool metadata 开始，再单调应用适用
的 core/global approval policy：core/global `auto` 不能削弱 registry `prompt` 或 `deny`。
其他 builtin handler 与 MCP call 保持各自文档定义的 resolution。Global fallback approval
不能把 terminal command guard 的 `prompt/high` 结果降级为自动执行；只有 `allow/low`
terminal command 可以自动批准，hardline block 会在 approval 前终止。

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

`tools` 匹配 tool name。`capabilities` 匹配 request 使用的 capability。`risks` 匹配
request risk。

## Tool Registry Metadata

`tools.metadata` 修改 registry metadata：

```yaml
tools:
  metadata:
    web_extract:
      approval_policy: deny
      risk: medium
      capability: network.fetch
```

Core metadata 不能降低 built-in tool 的限制。Authored 与 MCP registry entry 可被覆盖，
因为它们是 core-declared surface；但上面的 authored enforcement limitation 仍适用。

## 边界

Capability 本身不是 sandbox。Host 支持的 builtin 与 SDK 路径还会应用 workspace、
sensitive-path、command、approval、channel 与 tool rules。在默认 `host_shared` 模式中，
imported authored Python 可以在这些 SDK 路径之外使用普通 Python/OS APIs。目标
`EffectRuntime` 会集中 model-triggered effect policy，但不宣称提供 process isolation。
