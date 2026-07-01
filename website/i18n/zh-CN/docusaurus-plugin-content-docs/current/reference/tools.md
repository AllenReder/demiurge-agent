---
title: Tools 参考
description: 内置、authored 和 MCP tools 的参考说明。
---

# Tools 参考

host 会从 built-in toolsets、authored tools 和 MCP tools 构建一个可见的 tool registry。

## 内置 Toolsets

| Toolset | Examples |
| --- | --- |
| `coding` | `read_file`, `write_file`, `patch`, `search_files`, `terminal`, `job`, `process`, `web_extract`, `skills_list`, `skill_view`, `skill_manage`, `todo`, `clarify`, `session_search`. |
| `demiurge_control` | `tools_list`, `evolve_core`, `rollback_core`. |
| `schedule` | `schedule_manage`. |

## Authored Tools

authored tools 位于：

```text
agent/tools/<tool_id>/
```

它们使用 `slot.yaml` 加上一个 Python entrypoint，通常是：

```yaml
entrypoint: module:execute
```

```python
def execute(ctx, args):
    ...
```

文件名与 Agent Slot metadata 共用，但 authored tools 是 tools：它们是通过 host tool runtime 执行的 model-callable actions。

## 内置 Tools

| Tool | Purpose |
| --- | --- |
| `read_file` | 读取 workspace 内的文本。 |
| `write_file` | 替换一个 workspace file。 |
| `patch` | 应用一次精确的文本替换。 |
| `search_files` | 搜索文件内容或文件名。 |
| `terminal` | 在 workspace 内运行一个命令。 |
| `job` | 管理 background jobs。 |
| `process` | terminal background jobs 的兼容视图。优先使用 `job`。 |
| `web_extract` | 从已知 URL 获取并提取文本。 |
| `skills_list` | 列出 skill metadata。 |
| `skill_view` | 加载一个 skill 或已链接的 skill file。 |
| `skill_manage` | 创建、更新或删除 runtime-core skills。 |
| `todo` | 维护 per-session todo list。 |
| `clarify` | 向用户询问所需输入。 |
| `session_search` | 搜索或浏览本地 session messages。 |
| `schedule_manage` | 管理 core-authored schedule YAML。 |
| `tools_list` | 列出当前 core 可见的 tools。 |
| `evolve_core` | 通过 host 创建、gate 并 promote 一个 candidate core。 |
| `rollback_core` | 切回之前稳定的 core 版本。 |

`schedule_manage` 会创建带有显式默认值的 schedules，包括 enabled state、`base_input`、`base_output` 和 local delivery。runtime timezone 属于 host runtime，而不是单个 schedule YAML 文件。

## Background Jobs

`terminal(background=true)`、`ctx.agents.spawn(...)` 和 `evolve_core(background=true)` 会提交 host-owned 的 in-memory jobs。background tool calls 返回 `job_id`；terminal calls 还会返回 `process_id`，作为兼容别名。

`background=true` 默认等于 `notify_on_complete=true`。当 job 完成时，host 会在原始 session 中排队一个 synthetic model turn。如果用户 turn 已在运行，则完成会等待。如果用户输入和完成同时待处理，则先运行用户输入，并把待完成的 completion summaries 合并进那个用户 turn。`/stop` 只会取消 foreground turn；要停止 background job，请使用 `job(action="cancel", job_id="...")`。

`job` tool 支持：

| Action | Purpose |
| --- | --- |
| `list` | 列出 jobs，可按 `backend` 或 `owner_session_id` 过滤。 |
| `poll` | 返回一个 job 的 status、metadata、summary 和 log tail。 |
| `log` | 返回内存中的 job log。使用 `tail` 限制行数。 |
| `wait` | 等待最多 `timeout_seconds` 直到完成。 |
| `cancel` | 取消 queued 或 running 的 job。 |

Job statuses 为 `queued`、`running`、`blocked_needs_user`、`succeeded`、`failed`、`cancelled` 和 `lost`。completion payloads 包含 metadata、summary、result reference 和受限的 log tail；完整的 in-memory logs 可通过 `job(action="log")` 获取。

第一版实现只在内存中运行。正在运行的 jobs、logs 和待处理的 completion events 在 host process 退出时都会丢失。Jobs 会声明一个 `write_scope`；为了避免 foreground/background 或 background/background 的 overwrite races，另一个具有相同 scope 的活动 background job 会被拒绝。

## Package-Provided Web Search

`web_search` 不是默认 `coding` toolset 的一部分。它由 `web_search_brave` 或 `web_search_tavily` 之类的 provider packages 安装。

这两个 package 会暴露相同的 model-facing tool name `web_search`，但各自拥有 provider-specific request code 和分离的 libraries。由于两个 package 都会目标到 `agent/tools/web_search`，同一个 core 中一次只能安装一个 web search provider package。

`web_extract` 仍然是用于获取已知 URL 的 built-in tool。

## MCP Tools

MCP tools 来自以下声明：

```text
agent/mcp/*.yaml
```

host 会为 MCP tools 做 namespace 和 filter，然后再通过 capability 和 approval policy 运行它们。

## Output Policy

tool results 可以是 model-visible、current-turn-only，或者由 tool metadata 决定的形态。tool runtime 负责转换为 provider messages。

TUI 和 gateway display 可以通过以下方式控制：

```bash
uv run demiurge --tool-display quiet
uv run demiurge --tool-display summary
uv run demiurge --tool-display full
```

## Boundary

Agent Cores 可以声明 authored tools 和 MCP servers。host 负责 visible tool selection、dispatch、approval、workspace checks、result conversion 和 tool-call replay。
