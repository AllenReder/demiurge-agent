---
title: 工具运行时
description: 面向贡献者的 tool discovery、metadata、dispatch、approvals 与 results 说明。
---

# 工具运行时

当前 `ToolRuntime` 构建可见的 tool registry 并执行调用。它是冻结的 Host-owned
`EffectRuntime` 接口的前身，但 alpha 实现尚未为所有 effect 提供同一条 policy/dispatch
路径。参见 [Host 运行时契约](runtime-contracts.md#effectruntime)。

## Registry Sources

Tools 可以来自：

- built-in toolsets
- `agent/tools/` 下的 authored tools
- 从 `agent/mcp/*.yaml` 发现的 MCP tools

`agent.yaml` 选择 built-in toolsets，并可以覆盖 tool metadata。

## 当前 Dispatch

当前运行时解析 model tool name 后，分别进入 builtin、authored 或 MCP 分支。许多 builtin
handler 会自行执行 capability、approval、workspace、command 与 network checks，但目前
没有通用 builtin gate：`evolve_core` 与 `rollback_core` 当前会要求对应 capability，
却不会在修改 core refs 前解析 registry 的 `prompt` policy。MCP call dispatch 会应用
call capability 与 approval policy。Authored tool registry metadata 对 model 与 operator
可见，但 authored entrypoint 在 import 和调用前，尚未强制执行单数 `capability`、`risk`
与 `approval_policy` 字段。

MCP discovery 也会在 model execution 前准备。Catalog cache miss 时，当前运行时可能在
之后的 `mcp.call:*` capability 与 approval check 之前 spawn/connect 并调用
`list_tools()`。Registry display 与 execution 随后可能通过不同 lookup state 解析 MCP
tool。这些是已知 alpha 缺口，不是受支持的 extension point。

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
  Promotion 只会在 gates 通过后推进 Git refs。当前 alpha 分支会检查
  `tool.call:evolve_core`，但尚未在 promotion 前强制执行 registry `prompt` policy；
  `EffectRuntime` 必须补上这一缺口。
- `ctx.agents.spawn(...)` 由 runner 路由成 `agent.spawn` task。
- `delegate_task(...)` 由 active runner context 执行，并创建 `agent.spawn` task，child
  output 会作为 parent evidence 返回。两条路径都会在 task metadata 中记录 requested 与
  resolved child input/output slot 和 tool selection。

`task_list`、`task_status`、`task_control` 与 `yield_until` 是 model-facing runtime-task
controls。`task_control` 只支持 `command="cancel"`；其他命令会作为 unsupported 被拒绝。
Active execution 仍在 Host 运行时内进行，task status 与 logs 则通过
`RuntimeControlPlane` 从 SQLite projections 读取。

每个 background task 都记录 `kind`、owner session/turn、`source_tool`、status、summary、
bounded log tail、result reference 与可选 `write_scope`。具有相同非空 `write_scope` 的
新 active background task 会被拒绝。

## Authored Tools

Authored tools 的目标角色是 EffectRuntime adapters。目前它们与 builtins 共用 registry
discovery，但 authored entrypoint 仍会绕过上面所述的单数 registry capability/approval
metadata。其 `capabilities` list 对显式 `ctx.capability.require(...)` check 仍然有效。

## MCP Tools

MCP tools 使用 normalized server-prefixed names 与 include/exclude filters。Transport、
discovery、timeouts 与 result conversion 归 Host 所有，但当前 alpha 运行时尚未封闭
connect/discovery policy ordering 与 connection-bound dispatch。

目标 catalog 把每个可见 MCP definition 绑定到一个 session/revision connection 与一个
opaque effect reference。Call 绝不回退到全局 tool-name index。

## 边界

Agent Core 可以声明 tools，但它不拥有 tool-call replay、principal authorization 或
provider-specific tool message formatting。

`host_shared` authored Python 不是 sandbox。集中 model-triggered effect policy 并不能
阻止 imported Python 使用普通 Python 或 OS APIs；可选 subprocess/per-core isolation
是未来在同一 Host seam 上实现的 adapter。
