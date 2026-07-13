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
workspace scope、task control 与 result conversion 的产品 owner。现在由一个 per-turn
resolved catalog 同时生成 provider definitions、`tools_list` display、effective approval
metadata，以及 dispatch 使用的 adapter-bound `EffectRequest`。Builtin、authored 与 MCP call
不再按全局 tool name 做第二次 source lookup。MCP connect/discovery 现在拥有独立的
`mcp.connect:<server>` capability/approval gate，并在 client construction 前执行；后续 call
使用独立且 connection-bound 的 `mcp.call:<server>` path。

## Built-In Toolsets

| Toolset | Tools |
| --- | --- |
| `coding` | `read_file`, `write_file`, `patch`, `search_files`, `terminal`, `web_extract`, `skills_list`, `skill_view`, `skill_manage`, `todo`, `clarify`, `session_search` |
| `demiurge_control` | `tools_list`, `task_list`, `delegate_task`, `task_status`, `task_control`, `yield_until`, `evolve_core`, `rollback_core` |
| `schedule` | `schedule_manage` |

未知 toolset name 会导致 core loading 失败。

## Tool Name 唯一性

Model-visible tool name 在 builtin、authored 与 MCP source 之间必须唯一。Builtin/authored
collision 会让 core loading 失败；涉及已发现 MCP tool 的 collision 会让最终 catalog
构建失败。诊断会列出两侧 provenance，并要求重命名；Host 不会静默优先 builtin
implementation。这是针对过去依赖含糊 name 的 core 的 intentional alpha breaking change。

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

### Terminal environment 与 secret bindings

Terminal subprocess 从 environment allowlist 构建，而不是直接复制 `os.environ`。Host
保留基本 execution/locale/temp variables，把 `HOME` 指向专用 runtime directory，应用
已配置 timezone，并剥离 provider、channel、MCP、cloud 与 desktop credentials。显式
`env` overlay 必须 approval，并按 key/value 进入 command fingerprint；approval/event view
只暴露 key。

`pytest`、`python -m pytest`、`uv run`、`npm run`、`cargo test/build` 与 `make`
可能执行 repository code、plugin 或 build script，因此分类为 `prompt/high`，而不是无条件
safe command。Literal read-only command 仍可自动批准；`rg --pre`、`find -exec`、GNU
`sed` 的 `e` command，以及 Git external diff/textconv/pager option 等可执行形式同样分类为
`prompt/high`。Git auto-approval 只覆盖明确的 read-only shape；remote network operation
以及 branch、tag、remote、worktree mutation 都必须 approval，`--output` file write
也不例外。其他 read-oriented command 的 embedded write mode、inline environment
assignment 也必须 approval；system-time mutation 永远不会被当作 literal read。
Auto-approved executable name 必须是 bare command，不能是 workspace-relative path。
Wrapper/shell cwd change、command file、embedded read-path option 与未引用的 filename
expansion 都必须 approval。`.npmrc`、`.pypirc`、`.netrc`、`.aws/`、`.kube/`、`.ssh/`
与 `.env*` 等常见 credential path 属于 defense-in-depth sensitive path。

`terminal.secret_bindings` 是 object array，字段包括 `source`（`env:<NAME>`）、可选
`target` 与可选 `expires_in_seconds`。每个 source 都要求精确的
`secret.bind:<NAME>` capability。Binding 只允许 foreground 使用、不能超过 terminal
timeout、不会复用 session approval，并在完成后从 Host-side environment 移除。
Approval/audit view 记录 source、target、capability、expiry、实际 cwd、environment keys、
resolved shell/process executable 与 best-effort command executable，但不记录 value。Stdout/stderr 中与绑定值
完全相同的内容会替换为 `<redacted:TARGET>`。

`secret.bind:*` 等 wildcard grant 会被拒绝。Binding target 也不能替换 `PATH`、`HOME`、
`COMSPEC`、loader injection variables、language runtime search path 或 option hook 等
execution-control variable。最早 binding expiry 会收紧 foreground subprocess timeout；即使
请求的 command timeout 更长，expiry 也会终止 owned process tree。

### Terminal process 与 output lifecycle

Foreground 与 background terminal call 共用一个 Host-owned process lifecycle，并都注册到
Host shutdown。POSIX 创建新 session/process group，发送 TERM、等待短 grace deadline，再发送
KILL。Windows 先以 suspended 状态创建 process、分配 kill-on-close Job Object，再 resume。
Timeout、foreground turn cancellation、`task_control(command="cancel")` 与 Host shutdown 会终止
owned tree。Cancellation 是 single-flight，terminal state 会先持久化，再发布 completion
notification。Drain 或 task-log persistence failure 也会先清理 tree，再发布 failed task。

Host 会记录 PID、process-group id、platform、唯一 `spawn_id` 与 OS process-start marker。
Live cancellation 闭包持有 process handle，并在 PID/PGID fallback termination 前重新核对
start marker，不会仅凭 caller 提供或已陈旧的 PID 执行终止。该边界覆盖
仍留在 owned OS process tree 中的 descendants；对于显式创建新 session 或通过平台机制逃逸
tree boundary 的获批 `host_shared` code，它不是 hardened sandbox。

Stdout/stderr 会持续 drain。Foreground 每个 stream 最多保留 12,000 个字符的 tail，并记录
总 byte/character 数与 truncation metadata；不会先把完整输出物化到内存。Background terminal
output 以有界 chunk 写入 `task_logs`，相同 bounded-tail statistics 会进入 operator/debug task
metadata。同时，完整 stream 会增量写入 private durable terminal artifact，并注册到 runtime
artifact projection；background task 的 `result_ref` 会指向该 artifact。Exact bound secret 会在
artifact persistence 前 redaction。Artifact descriptor 包含 opaque `root` 与 stream-relative
path；Host 从 session identity 派生该 root，并强制它位于 `runtime/artifacts` 下。Artifact
open/write/flush/sync 失败时，pipe 会继续 drain，但 operation 最终失败。Durable log retention
仍是独立 runtime-store policy。

该 filtering/redaction 不是 OS isolation。获批 command 仍由 Host shell 执行；经过转换或
编码的 secret output 不在 exact-value redactor 的保证范围内。

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

对于 error result，`executionStarted`、`denial` 与 `approval` 是 Host 保留的 lifecycle
字段。Authored code 为这些 key 返回的值会被忽略：entrypoint 一旦被调用，Host 就记录
`executionStarted: true`，并根据自身 capability、approval 与 dispatch state 推导 typed
effect status。

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

1. 要求 `mcp.connect:<server_id>` 并解析 connect approval；
2. 只在 connect authority 允许时启动或连接 server；
3. 列出 server tools；
4. 应用 `tools.include` 与 `tools.exclude`；
5. 构建 `docs__search_docs` 之类的 safe name；
6. 通过与 built-in 和 authored tool 相同的 registry 暴露这些 tools。

MCP tool call 随后要求独立的 server call capability；除非 server manifest 设置
`capability`，否则默认为 `mcp.call:<server_id>`。

当前行为按 `connect_timeout_seconds` 限制 `list_tools()`；超时会关闭该 server connection、
记录 diagnostic，并继续处理后续 server。Discovery 在整个 runtime 内跨 session 最多并发
处理四个 server，并保持 deterministic naming。Discovery failure diagnostic 按 server 使用
30 秒 negative-cache TTL；在同一 catalog authority 内，过期后只重试该 server，健康 peer
connection 保持可用。Connect denial 会在下一个 turn 按 server 重新检查。Per-server
manifest fingerprint 只在整体 authority/core snapshot 不变时支持 targeted reconnect。
Catalog identity 还绑定 principal、capability snapshot、core revision 与 effective connect
policy；这些绑定变化时会驱逐整个旧 catalog。Configured cwd 必须在
approval/client construction 前解析到 Host workspace 内。
Declaration 变化会关闭旧 connection，并要求 connect reapproval 后才能启动 replacement
client。删除全部 declaration 会关闭剩余 connection。切换到新 session 或 resume 其他
session 时会跟踪驱逐旧 session；显式 session eviction 只关闭选定 session 的 catalogs。
Delegated child 使用 Host-issued authority，并在 child run 结束时关闭 MCP connection。
Connect approval preview 会显示安全 launch metadata，而不会包含 environment、header、URL
credential/query 或 argument secret value。Stdio child 使用共享 allowlisted environment，
并只在 connect approval 后添加 manifest-declared env entry。URL policy 仍属于后续独立
security 工作。

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

每个 action 都被归类为 high risk，并使用 registry `prompt` policy。MCP declaration
变化时，review 会记录准确 changed paths 与 secret-safe before/after security summary。
review 还会输出内容绑定的 `mcp-review:<sha256>` token。Model-visible promote 会把该
token 绑定到普通 promote approval；CLI/TUI caller 必须原样返回。缺失或已过期的确认不会
移动 Git refs。
`evolve_core` 会在
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
`task_control(command="cancel")`、`yield_until` 或 `task_list` 检查或控制 task。
Detail、wait、completion consumption 与 cancel 都受 admitted `PrincipalScope` 限制；
不属于该 scope 的 id 与不存在的 id 无法区分。这些 model-facing tool 只返回有界
status/result 字段，不接受 operator/debug view，也不返回 task log；完整 log 使用独立
Host/operator surface。Model task payload 会省略 owner id、write scope、任意 metadata 与
result reference，并限制 summary 长度。`task_list` 使用相同 projection，仍限制到当前
turn session。`/subagents` 使用相同 owner check。
如果 `yield_until` 返回 terminal 或 blocked status，该 tool result 会消费 task 的 pending
completion notification，因此相同结果不会再触发独立 background-completion turn。如果
`yield_until` 到达 timeout 时 task 仍在运行，它会返回带 `timed_out=true` 的当前 task
status；timeout 不表示 task failed。`task_list` 限制在 current session。

取消 background terminal task 时，会先把内存状态封为 cancelled，终止 owned process tree，
把 return code 与 exit reason 写入 durable terminal event，再发布 completion-ready。关闭 Host
会对 active runtime tasks 使用同一 cancellation path。

## Session History Search

`session_search` 在读取 history 前要求 `session.read` capability 与 resolved
`prompt/medium` approval。Explicit-session、browse 与 full-text path 都使用 owner-scoped
`SessionRuntime` query。普通 channel authority 只能读取当前绑定 session。本地 operator
只有通过显式、可审计的 operator scope 才能跨 session；不存在或未授权的 session id
返回相同外部错误。
含糊的 `legacy_local` session 在普通 owned query 中保持隐藏，只能通过专用 operator
repair/status path 查看；该 Host path 要求 reason 并持久化 audit。

Foreground `/stop` 只取消 foreground turn，不会取消 background tasks。

`agent.spawn` task metadata 同时包含 requested child slot controls，以及 child turn 运行后
解析出的 child pipeline slots。这些 operator-only metadata 不进入 model task payload；
请通过 owner-checked `/subagents <task_id>` Host/operator detail surface 检查。

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
