---
title: 工具运行时
description: 面向贡献者的 tool discovery、metadata、dispatch、approvals 与 results 说明。
---

# 工具运行时

当前 `ToolRuntime` 已包含冻结 Host-owned `EffectRuntime` 的第一段实现：builtin、authored
与 MCP model call 共用一个 per-turn resolved catalog 和 adapter-bound dispatcher。Adapter
result 会先归一化为最小 typed `EffectResult`/`EffectError`，turn loop 再转换为旧的
model-facing `ToolResult`；runtime event 会保留 typed status/error。Connect policy、扩展
lifecycle outcome、process/network lifecycle、
output limits 与 redaction 会在后续 EffectRuntime 工作中继续完成。参见
[Host 运行时契约](runtime-contracts.md#effectruntime)。

## Registry Sources

Tools 可以来自：

- built-in toolsets
- `agent/tools/` 下的 authored tools
- 从 `agent/mcp/*.yaml` 发现的 MCP tools

`agent.yaml` 选择 built-in toolsets，并可以覆盖 tool metadata。

## 当前 Dispatch

当前运行时为每个 turn 解析一次不可变 `ResolvedEffectCatalog`。Provider definitions、
`tools_list`、capability/approval metadata 与 dispatch 都使用该 catalog 的同一 entry。
`TurnEngine` 会把 provider tool call 转成携带精确 resolved entry 的 `EffectRequest`；dispatch
不会再次搜索 builtin definitions、authored slots 或全局 MCP name index。每个 entry 都绑定
source kind、core revision、adapter key、schema、capability、effective approval policy、risk
与 provenance。

统一 dispatcher 会先验证 core snapshot，并在选择 builtin、authored 或 MCP adapter 前执行
resolved capability。Workspace sensitivity、command review 等动态 builtin 检查仍会收紧该
policy；authored 与 MCP 保留各自的 approval summary。Core/global approval 在 catalog
解析时合并，且只能收紧 policy。Approval request 携带受限长、按字段名脱敏的 argument
preview。该 containment 不是后续 `EffectRuntime`/SEC-02 所属的最终 cross-effect
`SecretRedactor`。

`ToolRuntime.execute()` 只接受由其 catalog 拥有的 `EffectRequest`，并返回 typed
`EffectResult`。直接 Host caller 使用 `SessionTurnStepRunner.execute_call()` 完成一次 resolve，
或把已有 request 交给 `execute_tool()` 显式转换为 legacy result；不存在裸 `ToolCall`
execution fallback。

跨 source 的 tool name 必须唯一。Core loader 拒绝 builtin/authored collision；最终 catalog
拒绝涉及 MCP 的 collision。错误会同时报告两侧 provenance，并要求重命名 authored 或 MCP
tool，不存在隐式 builtin 优先级。

MCP discovery 也会在 model execution 前准备。Catalog cache miss 时，普通
`TurnExecution` 现在会先要求 `mcp.connect:<server>` 并解析 connect approval，然后才允许
client construction 或 `list_tools()`；后续 tool call 有独立的 `mcp.call:*` gate。Call
dispatch 仍绑定当前 turn/session entry。`list_tools()` 目前按 server 使用
`connect_timeout_seconds` 限时；超时 server 会被关闭，且不会阻止后续 server。Discovery
在整个 runtime 内跨 session 最多并发处理四个 server，并在之后确定性组装 name。Current
failure diagnostic 按 server 使用 30 秒 negative-cache TTL；在同一 catalog authority 内，
过期时只重试失败 server，健康 peer 保持连接，authority denial 则在下一个 turn 按 server
重新检查。Per-server manifest fingerprint 只在整体 authority/core snapshot 不变时支持
targeted reconnect。Configured cwd 会在 approval/client construction 前按 Host workspace
校验。Catalog identity 绑定 principal、capability snapshot、core revision 与 effective
connect policy，任一绑定变化都会在复用前驱逐整个旧 catalog。Declaration 变化也必须重新通过 connect
approval，replacement client 才能启动；删除全部 declaration 会关闭剩余 connection。
切换到新 session 或 resume 其他 session 时会跟踪驱逐旧 session。显式 session eviction 只关闭目标 session 的
catalogs；delegated child 使用 Host-issued scope，并在 child completion 时释放 connection。
Terminal subprocess 现在使用 allowlisted environment 与一次性的
capability/approval/expiry-bound secret injection；MCP stdio child 复用该 allowlist，并只添加
获批 manifest env entry。URL validation 仍属于后续 security 工作。旧 global
MCP tool-name index 已删除；call dispatch 只接受 connection-bound resolved entry。

Terminal preflight 会把 project-code execution 与 literal read-only command 分开，对显式
environment overlay 要求 approval，并构造包含实际 cwd、environment keys、resolved
shell/process 与 best-effort command executable、secret-binding metadata 的 audit view。Secret value 只在
approval 后解析，返回 stdout/stderr 中完全相同的值会被脱敏。Background terminal task
在 process/expiry lifecycle 能提供相同保证之前拒绝 secret binding。

Secret capability 使用 exact-default lookup，而不是普通 prefix wildcard matcher。Binding
target 会拒绝 execution-control variable，最早 binding deadline 会收紧 foreground
`subprocess.run()` timeout。

## 目标 EffectRuntime 接口

外部 Host seam 为：

```text
EffectRuntime.execute(EffectRequest, TurnExecutionContext) -> EffectResult
```

不可变 per-turn catalog 同时生成 provider-visible definitions 与 opaque resolved effect
reference。Execution 必须使用同一个 reference，不能再次执行全局 name lookup。

每个 builtin、authored 与 MCP effect 都遵循同一顺序：

1. 验证 request 与 resolved catalog binding；
2. 强制执行 `PrincipalScope` visibility 与 owner rules；
3. 要求不可变 capability snapshot；
4. 运行纯 workspace、command、URL、process、environment、namespace 与 output checks；
5. 解析 approval；
6. 只绑定显式授权的 secrets；
7. 在 deadline 与 cancellation 下调用选定 adapter；
8. cleanup、限制 streaming output、redact，并分别生成 model、operator、event 与 durable views。

对于 Host-mediated model-triggered effect，在适用的 capability 与 approval check 之前，
不得发生 authored tool import/invocation、subprocess spawn、MCP connect/discovery、file
mutation 或 network effect。这并不宣称能控制 already imported `host_shared` Slot code
直接发起的 Python/OS call。`mcp.connect:<server>` 与 `mcp.call:<server>` 是不同的
effects。

## Background Tasks

`ToolRuntime` 不拥有 background state。支持后台的 tool 会把 typed action 提交给 Host
运行时，并使用共享的 `RuntimeTaskWorker` 作为 active work 的 live worker：

- `terminal(background=true)` 创建 `terminal.exec` task，并把 stdout/stderr 捕获到
  `task_logs`。
- `evolve_core(action="start", background=true)` 创建 `evolver.run` task，编辑隔离的
  agents-tree worktree。它返回 task id；完成后的 task metadata/result 会标识 evolve
  run。它不会切换 live core。
- `evolve_core(action="review")`、`evolve_core(action="promote")` 与
  `evolve_core(action="discard")` 通过 Host-owned evolution runtime 操作该 run id。
  Promotion 只会在 gates 通过后推进 Git refs。MCP declaration 变化会在 review 中附带
  secret-safe 的 command/argument、URL、cwd、environment/header 名称、risk、approval 与
  capability diff；只有成功的 promote approval 同时确认 manual security review 后，才会
  允许推进 refs。每个 action 都会在 dispatch 前解析
  capability 与 approval；action 与 target 进入 approval-cache rule，因此一个 mutation
  action 不会授权另一个。该 cache 还绑定 admitted principal、session、core revision、
  capability snapshot、effective policy 与 effect entry；成功 promotion 或 rollback 会使
  对应 core 的 cached authority 失效。`EffectRuntime` 必须在不削弱该顺序的前提下删除
  剩余 dispatcher duplication。
- `ctx.agents.spawn(...)` 由 runner 路由成 `agent.spawn` task。
- `delegate_task(...)` 由 active runner context 执行，并创建 `agent.spawn` task，child
  output 会作为 parent evidence 返回。两条路径都会在 task metadata 中记录 requested 与
  resolved child input/output slot 和 tool selection。

`task_list`、`task_status`、`task_control` 与 `yield_until` 是 model-facing runtime-task
controls。`task_control` 只支持 `command="cancel"`；其他命令会作为 unsupported 被拒绝。
Active execution 仍在 Host 运行时内进行，task status 与 logs 存储在
`RuntimeControlPlane` 的 SQLite projections 中。Detail、wait、completion consumption
与 cancel 会在 store query 中使用 admitted `PrincipalScope`。Model-facing task control
只返回有界 status/result 字段，不能请求 `operator`/`debug` view；完整 task log 只通过
独立 Host/operator surface 读取。Model payload 会省略 owner id、write scope、任意
metadata、result reference 与 log，并限制 summary 长度。`task_list` 使用相同 model
projection 和限制到当前 turn session 的 store-side owned query。`/subagents` 只有经过
相同 owner check 后才使用完整 operator projection；猜中其他 principal 的 task id 与 id
不存在得到相同结果。
Runner-owned delegation control 与普通 ToolRuntime dispatch 调用同一个
`resolve_approval_scope(...)` seam，因此不能使用更弱的 execution identity 检查。

`session_search` 在读取任何 history 前要求 `session.read` 与 resolved `prompt/medium`
approval policy。Browse、explicit-session 与 full-text path 都使用 `SessionRuntime` owned
list/message query。普通 conversation scope 仅限当前绑定 session；audited operator scope
可在 approval 后搜索所有正常 owner session。含糊的 `legacy_local` session 被排除，必须
使用专用 operator repair/status path。

每个 background task 都记录 `kind`、owner session/turn、`source_tool`、status、summary、
bounded log tail、result reference 与可选 `write_scope`。具有相同非空 `write_scope` 的
新 active background task 会被拒绝。

## Authored Tools

Authored tools 是 EffectRuntime adapters。它们与 builtins、MCP 共用 per-turn resolved
catalog，dispatch 会在 import/invocation 前执行 singular registry capability/approval
metadata。其 `capabilities` list 仍是独立 grant surface，并对显式
`ctx.capability.require(...)` check 有效，且不能 self-grant singular dispatch gate。后续
EffectRuntime 工作会扩展 typed lifecycle outcome，并增加 output/redaction policy 与 adapter
lifecycle，但不会重新引入 name-based dispatch。

## MCP Tools

MCP tools 使用 normalized server-prefixed names 与 include/exclude filters。Transport、
discovery、timeouts 与 result conversion 归 Host 所有。每个可见 MCP definition 现在都
绑定当前 session/revision connection 与 resolved effect entry，因此 call dispatch 不会
回退到全局 tool-name index。Connect/discovery 使用独立的 pre-client capability 与
approval gate；session、authority 或 declaration 变化会 eviction stale connection。

## 边界

Agent Core 可以声明 tools，但它不拥有 tool-call replay、principal authorization 或
provider-specific tool message formatting。

`host_shared` authored Python 不是 sandbox。集中 model-triggered effect policy 并不能
阻止 imported Python 使用普通 Python 或 OS APIs；可选 subprocess/per-core isolation
是未来在同一 Host seam 上实现的 adapter。
