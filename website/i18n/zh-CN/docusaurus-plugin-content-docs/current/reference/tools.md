---
title: Tools 参考
description: Built-in、authored 与 MCP tools 参考。
---

# Tools 参考

Host 会为每个 turn 从以下来源构建可见 tool registry：

- `agent.yaml` 中的 built-in toolsets
- `slots.tools` 下的 authored tools
- 从 `slots.mcp` 发现的 MCP tools

Agent Core 声明 tool surface。Host 是 selection、dispatch、capability checks、approvals、
workspace scope、task control 与 result conversion 的产品 owner。当前 alpha 实现仍有
相互分离的 builtin、authored 与 MCP 路径。Authored dispatch 以及 `evolve_core` /
`rollback_core` mutation 分支现在会在 module import、background task 创建或 mutation
adapter 调用前，要求 resolved singular capability 并执行 approval policy；MCP
connect/discovery 仍发生在 call approval 之前。剩余的 dispatch duplication 与 MCP 缺口
已冻结为必须在
[Host 运行时契约](../developer-guide/runtime-contracts.md#effectruntime)中消除的目标。

## Built-In Toolsets

| Toolset | Tools |
| --- | --- |
| `coding` | `read_file`, `write_file`, `patch`, `search_files`, `terminal`, `web_extract`, `skills_list`, `skill_view`, `skill_manage`, `todo`, `clarify`, `session_search` |
| `demiurge_control` | `tools_list`, `task_list`, `delegate_task`, `task_status`, `task_control`, `yield_until`, `evolve_core`, `rollback_core` |
| `schedule` | `schedule_manage` |

未知 toolset name 会导致 core loading 失败。

## Built-In Tool Metadata

Built-in tool 的 risk、capability 与 approval default 由 Host 定义。例如：

| Tool | Capability | Registry approval metadata |
| --- | --- | --- |
| `read_file` | `fs.read` | 非 sensitive workspace read 为 `auto`；workspace 外或 sensitive path 为 `prompt` |
| `write_file` | `fs.write` | `prompt` |
| `patch` | `fs.write` | `prompt` |
| `terminal` | `terminal.exec` | `prompt` |
| `web_extract` | `network.fetch` | `prompt` |
| `schedule_manage` | `schedule.manage` | `prompt` |
| `evolve_core` | `tool.call:evolve_core` | `prompt` |
| `rollback_core` | `tool.call:rollback_core` | `prompt` |

Terminal 进入 approval 前，Host 会同时对 execution-faithful raw command，以及额外的
ANSI-stripped/NFKC detection candidates 做 lexical review。Normalization 只增加检查，
不会替换 raw shell interpretation。只有被识别为 literal 且分类为 `allow/low` 的
command 才能自动批准。Command substitution（`$()` 与 backticks）、process
substitution、parameter/arithmetic expansion（包括旧式 `$[...]`）、无法解析的 shell
form 和 unknown command 都保持 `prompt/high`；global `auto` fallback 不能降低该
command-guard decision。已知 destructive payload 会在调用 approval provider 前被阻断。
Scanner 能证明为 literal 的 single-quoted 或 escaped metacharacter 仍可按 literal 处理。

该 lexical guard 是 containment，不是 shell sandbox，也不是完整 shell AST。显式获批的
command 仍由 Host terminal runtime 执行。Ambiguous shell approval 使用 command、cwd、
显式 environment overlay，以及 foreground/background mode、timeout 等 execution
options 的 fingerprint。因此一次 approval 不会通过同一粗粒度 rule key 授权不同的
execution shape；该 fingerprint 不能替代独立的 session/principal ownership contract。
Ambiguous shell text（包括 comment 中的 expansion syntax）可能会保守地要求 approval。

对于使用 approval resolution 的 builtin handler，core metadata 可以让 effective policy
更严格，但不能降低 risk 或削弱 registry policy。该规则包括所有 `evolve_core` action 与
`rollback_core`。

## Authored Tools

Authored tool 位于 `slots.tools` 配置的 root 下，通常为：

```text
agent/tools/<tool_id>/
  tool.yaml
  module.py
```

如果省略 `slots.tools`，不会发现 authored tools。

`tool.yaml` 接受以下字段：

| 字段 | 默认值 | 含义 |
| --- | --- | --- |
| `entrypoint` | `module:execute` | 从 tool directory 加载的 callable。 |
| `description` | `""` | Model-visible tool description。 |
| `input_schema` | `{}` | Model-visible JSON schema。 |
| `risk` | `medium` | Authored approval resolution 使用的 registry risk。 |
| `capability` | `null` | 非 null 时在 module import 前要求的 primary registry capability。 |
| `approval_policy` | `prompt` | Module import 与 invocation 前解析的 tool-level policy。 |
| `display_policy` | `summary` | Operator display hint。 |
| `model_output_policy` | `content` | Model-output conversion hint。 |
| `capabilities` | `[]` | Implementation 可通过 `ctx.capability.require(...)` 要求的 capabilities。 |

`tool.yaml` 不接受 `failure_policy`、`history_policy`、`default_placement` 或
`timeout_seconds` 等 slot-only 字段。

单数 `capability` 与 `capabilities` list 含义不同：

- `capability` 在 registry 与 approval metadata 中标识 tool，并且必须由 core default 或
  path-scoped Host capability configuration grant；
- `capabilities` 向 tool implementation 授予 effect capabilities。

Plural list 不能满足 singular dispatcher gate；authored tool 不能通过在该 list 中重复
singular value 来授权自己的 invocation。

Authored dispatcher 使用与 definitions 相同的 resolved registry entry：非空 singular
`capability` 会先被要求，随后在 import/call entrypoint 前应用 `risk`、
`approval_policy` 与更严格的 core/global approval policy。Approval request 使用受限长、
按字段名脱敏的 argument preview，而不是 raw arguments。`capabilities` list 仍是独立
grant surface，并在 authored code 或 SDK client 调用 `ctx.capability.require(...)` 时执行。
由于 `host_shared` Python 也可以直接调用普通 Python/OS APIs，这些 declaration 并不是
sandbox。

Authored tools 不会列在 `agent/pipelines.yaml` 中。

## Authored Tool Runtime

默认 entrypoint：

```python
def execute(ctx, args):
    ...
```

Host 传入包含以下内容的 `ToolContext`：

| Attribute | 含义 |
| --- | --- |
| `ctx.turn` | 当前 turn metadata。 |
| `ctx.slot_id` | Tool id。 |
| `ctx.slot_path` | 相对 tool path，例如 `agent/tools/project_note`。 |
| `ctx.capability` | 用于 `can(...)` 与 `require(...)` 的 capability facade。 |
| `ctx.output` | Tool 在 active turn 内调用时的 delivery client。 |
| `ctx.workspace` | 解析后的 workspace root。 |

返回 `demiurge.sdk.ToolResult`、兼容 dict，或任何可转换成文本的值。

`ToolResult.content` 是默认 model-visible result。`model_output` 覆盖 model 所见内容，
`display_output` 覆盖 operator UI 与 channel 的 tool card 内容。对于 `terminal`，display
output 会先显示执行的 command 与 cwd，再显示 exit code、stdout 与 stderr；model-visible
result 保持现有 exit/output shape。

## MCP Tools

MCP server 位于配置的 MCP root 下，通常为：

```text
agent/mcp/<server_id>.yaml
```

对于每个 enabled server，Host 会：

1. 启动或连接 server；
2. 列出 server tools；
3. 应用 `tools.include` 与 `tools.exclude`；
4. 构建 `docs__search_docs` 之类的 safe name；
5. 通过与 built-in 和 authored tool 相同的 registry 暴露这些 tools。

MCP tool call 要求 server capability；除非 server manifest 设置 `capability`，否则默认
为 `mcp.call:<server_id>`。

当前 alpha 限制：步骤 1 和 2 在准备 catalog 时发生，早于之后的 `mcp.call:*`
capability 与 approval check。未来的 `mcp.connect:<server_id>` effect 会单独管理
spawn/connect/discovery。

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

支持的 metadata key：

- `risk`
- `capability`
- `approval_policy`
- `model_output_policy`
- `display_policy`
- `enabled`

## Built-In Skill Tools

`skills_list` 列出 skill metadata。`skill_view(name)` 加载 skill 的 `SKILL.md`；
`skill_view(name, file_path)` 加载 `references/`、`templates/`、`scripts/` 或 `assets/`
下的链接文件。

`skill_manage` 会在 active runtime core 配置的 skills root 中写入 skill。它支持：

- `create` 与 `update`：完整写入 `SKILL.md`；
- `patch`：在 `SKILL.md` 或 support file 中执行 `old_string` / `new_string` 替换；
- `write_file` 与 `remove_file`：操作 `references/`、`templates/`、`scripts/` 或
  `assets/` 下的 support files；
- `delete`：从 runtime core 删除 skill。

每次 `skill_manage` 写入都需要 `fs.write` approval。Host 会拒绝 absolute path、parent
traversal、hidden path segment，以及写入 configured skills root 之外的操作。变更从后续
turn 生效；当前 turn 不会 hot-reload active core。

## Core Evolution Tools

`evolve_core` 是具有四个 action 的单一 model-visible tool：

| Action | 必填字段 | Effect |
| --- | --- | --- |
| `start` | `goal` | 创建 `.evolve/runs/<run_id>/agents` 并运行 Host-managed evolver。 |
| `review` | `run_id` | 运行 Host-owned gates 并写入 `refs/demiurge/runs/<run_id>`。 |
| `promote` | `run_id` | 重新运行 gates，并推进 `refs/demiurge/previous` 与 `refs/demiurge/live`。 |
| `discard` | `run_id` | 删除 run worktree 与 metadata。 |

每个 action 都被归类为 high risk，并使用 registry `prompt` policy。`evolve_core` 会在
foreground adapter call 或 background task 创建前要求 resolved capability 与 approval；
`rollback_core` 也会在调用 version store 前执行同样顺序。Approval rule 按 action
隔离，因此 `promote` 的 cached allow 不会授权 `discard`、`review`、`start` 或 rollback。
Session allow 会绑定 Host-issued principal、session、core、effective policy，以及
capability/core snapshot fingerprint；它有有界 TTL，并会在 owner revoke、session
replacement、core change 或 app close 时失效。`rollback_core` 会为 live Agent Core
tree 创建新的 rollback commit；新 revision 从下一个 turn 生效。

## Child Agent Controls

Authored slot 可以同步或在后台调用 child agent：

```python
result = await ctx.agents.run(
    "evolver",
    "child prompt",
    input_slots=["base_input"],
    output_slots=["base_output"],
    tools="all",
    use_bootstrap=False,
)

handle = ctx.agents.spawn(
    "evolver",
    "child prompt",
    input_slots="all",
    output_slots="all",
    tools=["tools_list"],
    use_bootstrap=True,
)
```

`ctx.agents.run(...)` 等待 child turn，并返回 `AgentRunResult`。
`ctx.agents.spawn(...)` 返回 `agent.spawn` background task 的 `AgentSpawnHandle`。

`input_slots` 与 `output_slots` 接受：

| 值 | 含义 |
| --- | --- |
| 省略、`None` 或 `[]` | 只运行 `base_input` 或 `base_output`。 |
| `"all"` | 运行 child core 配置的完整 pipeline，包括 parallel slots。 |
| 非空 list | 按 slot id 过滤 child core 的 active pipeline，保持 pipeline order 与 serial/parallel grouping。 |

Slot id 必须存在且已经位于 child core 的 active pipeline 中。无效 id 会使 authored
`ctx.agents` call 抛出 `ValueError`。

`tools` 控制 child turn 可见且可执行的 tool set：

| 值 | 含义 |
| --- | --- |
| 省略、`None` 或 `"all"` | 使用 child core 配置的 tools。 |
| `"none"` 或 `[]` | 运行不带 tools 的 child turn。 |
| 非空 list | 只允许 child core configured tools 中列出的 tool ids。 |

Tool selection 只能缩小 child core configured tools；它不会授予缺失 tool 或 capability
grant。Builtin、authored 与 MCP call policy 在 child turn 中仍适用。无效 tool id 会使
authored `ctx.agents` call 抛出 `ValueError`。

`use_bootstrap` 默认为 `False`。为 false 时，child turn 不会运行 bootstrap slots、创建
bootstrap snapshot，也不会把现有 bootstrap snapshot 注入 provider request。设置
`use_bootstrap=True` 后使用 child core 的正常 bootstrap pipeline。

`delegate_task(...)` 向 model 暴露同样的 child controls：

```text
delegate_task(
  goal,
  core_id=None,
  context_mode="isolated",
  notify_policy="return_to_parent",
  max_depth=None,
  tools="all",
  input_slots=["base_input"],
  output_slots=["base_output"],
  use_bootstrap=False,
)
```

对于 `delegate_task`，无效 child slot 或 tool selection 会返回 tool error result，而不是
向 authored slot code 抛出异常。

## Background Runtime Tasks

以下调用会提交 Host-owned background tasks：

- `terminal(background=true)`
- `delegate_task(...)`
- `ctx.agents.spawn(...)`
- `evolve_core(action="start", background=true)`

Background task tool 返回 `task_id`。使用 `task_status`、
`task_control(command="cancel")`、`yield_until` 或 `task_list` 检查或控制 task。如果
`yield_until` 返回 terminal 或 blocked status，该 tool result 会消费 task 的 pending
completion notification，因此相同结果不会再触发独立 background-completion turn。如果
`yield_until` 到达 timeout 时 task 仍在运行，它会返回带 `timed_out=true` 的当前 task
status；timeout 不表示 task failed。`task_list` 限制在 current session。

Foreground `/stop` 只取消 foreground turn，不会取消 background tasks。

`agent.spawn` task metadata 同时包含 requested child slot controls，以及 child turn 运行后
解析出的 child pipeline slots。使用 `task_status` 或 `yield_until` 检查这些字段。

## Package-Provided Web Search

`web_search` 不属于默认 `coding` toolset。它由 `web_search_brave` 或
`web_search_tavily` 等 provider package 安装。

两个 package 都暴露 model-facing tool name `web_search`。因为两者都指向
`agent/tools/web_search`，一个 core 同时只能安装一个 web search provider package。

`web_extract` 仍是获取已知 URL 的 built-in tool。

## 检查可见 Tools

使用 built-in tool：

```text
tools_list
```

或使用 TUI command：

```text
/tools
```

可以在启动时调整 tool display：

```bash
uv run demiurge --tool-display quiet
uv run demiurge --tool-display summary
uv run demiurge --tool-display full
```
