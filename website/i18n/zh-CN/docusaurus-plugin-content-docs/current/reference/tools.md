---
title: Tools 参考
description: Built-in、authored 和 MCP tools 的参考。
---

# Tools 参考

Host 会在每个 turn 中从以下来源构建可见 tool registry：

- `agent.yaml` 中的 built-in toolsets
- `slots.tools` 下的 authored tools
- 从 `slots.mcp` 发现的 MCP tools

Agent Core 声明 tool surfaces。Host 拥有 selection、dispatch、capability checks、approvals、workspace scope、task control 和 result conversion。

## Built-In Toolsets

| Toolset | Tools |
| --- | --- |
| `coding` | `read_file`, `write_file`, `patch`, `search_files`, `terminal`, `run_terminal`, `web_extract`, `skills_list`, `skill_view`, `skill_manage`, `todo`, `clarify`, `session_search` |
| `demiurge_control` | `tools_list`, `task_list`, `delegate_task`, `task_status`, `task_control`, `yield_until`, `evolve_core`, `rollback_core` |
| `schedule` | `schedule_manage` |

未知 toolset names 会导致 core loading 失败。

## Built-In Tool Metadata

Built-in tools 有 host-defined risk、capability 和 approval defaults。例如：

| Tool | Capability | Default approval |
| --- | --- | --- |
| `read_file` | `fs.read` | non-sensitive workspace reads 为 `auto` |
| `write_file` | `fs.write` | `prompt` |
| `patch` | `fs.write` | `prompt` |
| `terminal` | `terminal.exec` | `prompt` |
| `web_extract` | `network.fetch` | `prompt` |
| `schedule_manage` | `schedule.manage` | `prompt` |
| `evolve_core` | `tool.call:evolve_core` | `prompt` |
| `rollback_core` | `tool.call:rollback_core` | `prompt` |

Core metadata 可以让 built-in tools 更严格，但不能降低它们的 risk 或弱化它们的 approval policy。

## Authored Tools

Authored tools 位于 `slots.tools` 配置的 root 下，通常是：

```text
agent/tools/<tool_id>/
  tool.yaml
  module.py
```

如果省略 `slots.tools`，则不会发现 authored tools。

可接受的 `tool.yaml` 字段是：

| 字段 | 默认值 | 含义 |
| --- | --- | --- |
| `entrypoint` | `module:execute` | 从 tool 目录加载的 callable。 |
| `description` | `""` | Model-visible tool description。 |
| `input_schema` | `{}` | Model-visible JSON schema。 |
| `risk` | `medium` | Registry risk metadata。 |
| `capability` | `null` | 这个 tool 的 approval metadata 使用的 primary registry capability。 |
| `approval_policy` | `prompt` | Tool-level approval metadata。 |
| `display_policy` | `summary` | Operator display hint。 |
| `model_output_policy` | `content` | Model-output conversion hint。 |
| `capabilities` | `[]` | Implementation 可通过 `ctx.capability.require(...)` 需要的 capabilities。 |

`tool.yaml` 不接受 slot-only fields，例如 `failure_policy`、`history_policy`、`default_placement` 或 `timeout_seconds`。

单数 `capability` 与 `capabilities` 列表是分开的：

- `capability` 在 registry 和 approval metadata 中标识 tool。
- `capabilities` 向 tool implementation 授予 effect capabilities。

Authored tools 不会列在 `agent/pipelines.yaml` 中。

## Authored Tool Runtime

默认 entrypoint 是：

```python
def execute(ctx, args):
    ...
```

Host 传入一个 `ToolContext`，包含：

| Attribute | Meaning |
| --- | --- |
| `ctx.turn` | 当前 turn metadata。 |
| `ctx.slot_id` | Tool id。 |
| `ctx.slot_path` | 相对 tool path，例如 `agent/tools/project_note`。 |
| `ctx.capability` | 用于 `can(...)` 和 `require(...)` 的 capability facade。 |
| `ctx.output` | 当 tool 在 active turn 中被调用时可用的 delivery client。 |
| `ctx.workspace` | 解析后的 workspace root。 |

返回 `demiurge.sdk.ToolResult`、兼容的 dict，或任何可转换为 text 的值。

## MCP Tools

MCP servers 位于配置的 MCP root 下，通常是：

```text
agent/mcp/<server_id>.yaml
```

对于每个 enabled server，host 会：

1. 启动或连接到 server。
2. 列出 server tools。
3. 应用 `tools.include` 和 `tools.exclude`。
4. 构建安全名称，例如 `docs__search_docs`。
5. 通过与 built-in 和 authored tools 相同的 registry 暴露这些 tools。

MCP tool calls 需要 server capability，默认为 `mcp.call:<server_id>`，除非 server manifest 设置了 `capability`。

## Tool Metadata Overrides

使用 `agent.yaml`：

```yaml
tools:
  metadata:
    web_extract:
      approval_policy: deny
    project_note:
      risk: low
      enabled: false
```

支持的 metadata keys 是：

- `risk`
- `capability`
- `approval_policy`
- `model_output_policy`
- `display_policy`
- `enabled`

## Background Runtime Tasks

这些 calls 会提交 host-owned background tasks：

- `terminal(background=true)`
- `run_terminal(...)`
- `delegate_task(...)`
- `ctx.agents.spawn(...)`
- `evolve_core(background=true)`

Background task tools 会返回 `task_id`。使用 `task_status`、`task_control(command="cancel")`、`yield_until` 或 `task_list` 检查或控制它们。

Foreground `/stop` 只会取消 foreground turn。它不会取消 background tasks。

## Package-Provided Web Search

`web_search` 不是默认 `coding` toolset 的一部分。它由 `web_search_brave` 或 `web_search_tavily` 等 provider packages 安装。

两个 packages 都暴露面向模型的 tool name `web_search`。因为两个 packages 都以 `agent/tools/web_search` 为目标，所以每个 core 一次只安装一个 web search provider package。

`web_extract` 仍是用于获取已知 URL 的 built-in tool。

## 检查可见 Tools

使用 built-in tool：

```text
tools_list
```

或使用 TUI 命令：

```text
/tools
```

Tool display 可以在启动时调整：

```bash
uv run demiurge --tool-display quiet
uv run demiurge --tool-display summary
uv run demiurge --tool-display full
```
