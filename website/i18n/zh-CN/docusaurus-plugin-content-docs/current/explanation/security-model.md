---
title: 安全模型
description: 理解 workspace scope、approvals、capabilities、secrets 与 channel trust。
---

# 安全模型

Demiurge 把 capabilities 与危险的 model-triggered effects 视为 Host-owned。受支持的
`ctx.*`、builtin-tool 与 MCP-call 路径通过 Host 接口请求 effect。在默认
`host_shared` 运行时中，imported Agent Core Python 是可信代码，也能使用普通 Python/OS
API；当前 alpha 运行时不是 sandbox。

## Workspace Scope

File write、patch 与 terminal working directory 都限制在解析出的 workspace 中。
Workspace 可以来自 process override、environment variable、core manifest、local run
context，或 fallback `~/.demiurge/workspace`。

Built-in file read 可以读取 workspace 之外 Host 可见的路径。这类 workspace 外读取以及
所有 sensitive read，都必须在打开文件前获得 approval。

Workspace scope 不是唯一防线。Sensitive path 与危险操作仍可能需要 approval 或被拒绝。

## Terminal Command Containment

Terminal command guard 会同时检查 execution-faithful raw command 与额外的 ANSI/NFKC
detection candidates。Literal `allow/low` command 可以使用 automatic approval；可执行或
无法建模的 shell expansion、nested shell evaluation、malformed shell syntax 与 unknown
command 都保持 `prompt/high`，global `auto` policy 不能削弱该结果。已知 destructive
hardline payload 会在 approval 前被阻断。

该 scanner 有意采用 fail-closed 策略，因此 ambiguous text（包括 comment 中类似
expansion 的 syntax）可能触发 prompt。它是 containment，不是完整 shell parser 或
sandbox。显式获批的 command 仍由 Host terminal runtime 执行。Terminal subprocess
现在从 Host allowlist 构建环境，而不是继承完整 process environment；它使用专用 runtime
`HOME`，并默认剥离 provider、channel、MCP、cloud 与 desktop credentials。执行
workspace/project code 的 command，以及任何显式 environment overlay，即使外层是已知
development command，也必须 approval。Process-tree control 仍是独立边界。

## Capabilities

Capabilities 描述以下 effect class：

- `fs.read`
- `fs.write`
- `terminal.exec`
- `secret.bind:<ENV_NAME>`
- `task.control`
- `network.fetch`
- `schedule.manage`
- `tool.call:evolve_core`
- `tool.call:rollback_core`

Builtin file、terminal、network、schedule 与 skill handlers 会在受保护操作前解析适用的
capability/approval checks，MCP tool call 也会在 call 前执行。Authored tool dispatch
现在会在 module import/invocation 前要求 resolved singular capability 并解析 approval
policy。剩余 alpha 缺口包括：MCP spawn/connect/discovery 可能发生在 call approval 之前；
而 builtin/authored/MCP dispatch 仍使用不同实现分支。`evolve_core` / `rollback_core`
现在会使用同一个 resolved registry entry，在 adapter call 或 background task 创建前执行
capability 与单调收紧的 approval policy。目标 `EffectRuntime` 会用同一套顺序删除剩余
分支 duplication；参见
[Host 运行时契约](../developer-guide/runtime-contracts.md#effectruntime)。

Background completion turn 使用原 session 的正常 capabilities，不会仅仅因为在后台运行
就获得 approval。`evolve_core(action="start", background=true)` 必须在 Host 创建 runtime
task 前通过 resolved capability 与 action-specific approval。

## Secrets

Provider secret 应放在 Host config、environment variables 或 `~/.demiurge/.env` 中。
Status command 应报告 secret source，但不打印 secret value。

Terminal 不会继承这些值。Foreground call 只有在 active capability snapshot 授予
`secret.bind:<NAME>` 时，才能请求 source 为 `env:<NAME>` 的一次性
`secret_bindings`。Host 会 prompt、把 binding 的有效期限制在 terminal timeout 内、拒绝
background 使用，只记录 source/target/capability/expiry metadata，并把 stdout/stderr 中
与绑定值完全相同的内容替换为 redaction marker。这是受控注入，不是 sandbox，也不保证
阻止经过转换或编码后的泄露。

Capability 必须精确（`secret.bind:*` 不匹配），binding 也不能在 approval 后覆盖
`PATH`、`HOME`、shell/loader control 或 language runtime search path。最早 binding
expiry 会缩短 foreground subprocess timeout；descendant process-tree cleanup 仍是独立
lifecycle boundary。

类型为 `secret` 的 package component option 可以写入 component-local config，但
`packages.yaml` 只保存脱敏后的 option value。该文件中的 package provenance hash 用于
drift reporting 与 uninstall safety；runtime truth 仍是已提交的 agents tree。

## Channels

External channel 默认禁用。Channel bridge 必须在接受 inbound event 前验证 token、
signature、allowlist 或 room/user constraint。

Telegram 通过 `allowed_users` 与 `allowed_chats` 默认拒绝。

## Non-Goals

当前 alpha 运行时不承诺 hardened multi-tenant sandbox。Agent Slot 代码默认运行在
host-shared Python environment 中。Per-core environment 与 subprocess worker 是未来
isolation option，不是默认运行模式。Capability grant 不授予 session/operator authority；
approval cache 现在强制执行 admitted `PrincipalScope`、session、core/capability policy
fingerprint、bounded lifetime 与显式 revocation，tool argument 不能声明另一个 owner。
Session browse/resume/search 与 task detail/wait/cancel 现在已在 store-owned query 中执行
同一 scope；`session_search` 还要求 `session.read` 与 approval。后续统一 EffectRuntime
仍需把该 seam 扩展到每个 effect adapter，因此当前 alpha 尚未实现全路径统一 enforcement。Runtime
task records、logs、scheduler instances 与 delivery outbox status 存储在 SQLite runtime
database 中；in-process worker 仍负责 live execution，并且不会在 Host process restart
后重放已经开始的危险 side effect。

含糊迁移 session 使用 `legacy_local` owner kind。普通 channel/operator session/history
query 对这些 row fail closed；检查只保留给显式 operator repair/status path。Model-facing
task tool 也不能选择 operator/debug view 或接收 task log。
Repair/status path 仅属于 Host，要求精确 lookup 与有界 operator reason，并写入 durable
audit event。失败的精确 owned lookup 也会在 Host audit 中保留真实原因，同时对外继续使用
不可区分的统一错误。
