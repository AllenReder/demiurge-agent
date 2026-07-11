---
title: Host 运行时契约
description: 面向贡献者冻结 turn、effect、context、principal scope 与持久 channel ingress 的契约。
---

# Host 运行时契约

本页冻结后续加固阶段必须实现的贡献者接口。这是一份设计契约，并不表示当前
alpha 运行时已经强制满足所有不变量。在本页之前加入的回归测试会在实现仍不满足
要求的地方有意保持红色。

这些契约保留以下产品边界：

- Host 拥有 harness、authority、危险 effect、持久化、delivery、promotion 与 rollback；
- Agent Core 拥有 `agent.yaml + agent/`；
- Agent Slot 仍是 authored logic seam；
- `host_shared` 仍是默认的 authored Python 运行时；
- candidate dependency change 仍需人工审查；
- Git-backed core revision、promotion 与 rollback 仍由 Host 控制。

## 契约词汇

本页术语均有明确含义：

- **module** 通过一个接口隐藏实现；
- **interface** 不仅包含输入与输出，也包含不变量、顺序、错误、取消、重启与性能语义；
- **seam** 是调用方和测试跨越接口的位置；
- **adapter** 在某个 seam 上实现接口。

下面四个 Host module 是 Host 调用方的外部 seam。它们的 helper object、store、
transport 与 test fake 属于内部 seam。Agent Core 作者不会直接调用这些接口，而是继续
使用精简的 `ctx.*` SDK 和 model-visible tools。

| Module | 冻结的外部接口 | 当前实现前身 |
| --- | --- | --- |
| `TurnExecution` | `run(TurnRequest) -> TurnResult`；`cancel(TurnId, PrincipalScope) -> CancelResult` | `SessionTurnStepRunner.run_turn()` 与 `TurnPipelineRuntime.run()` |
| `EffectRuntime` | `execute(EffectRequest, TurnExecutionContext) -> EffectResult` | `ToolRuntime`、security helpers、`McpRuntime` 与内联 process/network 代码 |
| `ContextManager` | async `prepare(ContextRequest) -> PreparedContext`；async `observe(UsageObservation) -> None` | `ContextAssembler`、`PromptContextRuntime` 与 `SessionCompactionRuntime` |
| `ChannelInbox` | `accept(InboundEnvelope) -> InboxReceipt`；`claim() -> ClaimedInbound`；`complete(...)`；`fail(...)` | 目前没有持久 inbound owner |

这里冻结的是名称、ownership 与行为语义。具体的私有 class 布局与存储 schema 仍是实现细节。

## PrincipalScope

`PrincipalScope` 是进入 session、task、approval、effect 与 history 操作的不可变 Host
authority。它不是 capability grant，也绝不能只根据不受信任的 payload 字段构造。

最少包含以下逻辑字段：

```text
principal_id
authority: conversation | operator | system | delegated_agent
channel
conversation_key
session_id
allowed_session_ids: frozenset
```

Host 根据已认证的 adapter facts 与持久 conversation/session bindings 派生该值：

- channel conversation 通常只拥有其绑定的 session；
- CLI/TUI 的跨 session 操作需要显式 operator authority；
- schedule 获得仅限其 scheduled run/session 的 system authority；
- child agent 拥有自己的 child session，而不是 parent session；
- `CapabilityFacade` 描述 Agent Core effect grant，绝不能代替 principal authorization。

只有 Host authority resolver/factory 能构造 `PrincipalScope`。Transport adapter 只贡献
已认证事实；request payload 与 Agent Core 代码不能实例化 operator/system authority。
缺少 ownership 或 ownership 含糊的 legacy session/task row 会 fail closed，只能通过显式
operator repair path 查看。

每个 detail、list、wait、cancel、history、resume、search 与 approval cache 操作，都由
所属 module/store 应用 owner predicate。调用方不得先按 id 读取全局对象，再临时执行
owner check。

`always_allow_for_session` cache key 至少为
`(principal_id, session_id, policy_fingerprint, rule_key)`。Fingerprint 覆盖 core
revision、capability snapshot、effective approval policy 与相关 effect entry。Session
结束、authority 或 conversation-binding 改变、policy/revision 改变、显式 revocation 或
有界 expiry 都会使 entry 失效。Session-scoped approval 不会变成进程范围的环境
authority。

## TurnExecutionContext

`TurnExecutionContext` 只创建一次：admission 解析 authority 并取得 session lease
之后创建。在一个 turn 的整个生命周期内，它的 bindings 必须深度不可变。仅把 dataclass 标成 frozen，
但其中仍含 mutable dict、list、`LoadedCore` 或 mutable runner reference，不符合本契约。

最少包含以下逻辑绑定：

```text
request_id
turn_id
principal: PrincipalScope
session_id
core_id
core_revision
capability_snapshot
workspace_scope
route_token
admission_lease_token
cancellation_token
trace_ids
interaction_metadata
```

Lease 与 cancellation 字段是不可变 identity，不是 mutable control object。所属 Host
module 会在内部 seam 后保留 live lease 与 monotonic cancellation state；调用方不能通过
context 修改它们。

这些 binding 必须相互一致：`principal` 必须授权 `session_id`；capability snapshot 必须
属于 pinned `core_id`/`core_revision`；route、admission 与 cancellation token 都必须指向
同一个 request/turn/session，且不能跨 turn 复用。`interaction_metadata` 必须是有界、
脱敏且深度不可变的值，而不是 transport-owned mutable dict。

Secret values、mutable stores、provider clients、approval caches 与 adapter implementations
不是 public context 字段。它们仍作为所属 Host module 的 injected dependencies。

Agent Slot 与 authored tool 继续接收现有的精简 author-facing SDK contexts；适用时，
这些 context 会包含 `TurnContext`。它们不会获得 `PrincipalScope`、route internals、
admission leases、Host stores 或 operator authority。

## TurnExecution

### 接口

```text
TurnExecution.run(TurnRequest) -> TurnResult
TurnExecution.cancel(TurnId, PrincipalScope) -> CancelResult
```

`TurnRequest` 只包含深度不可变的值：

- raw input 与有界 attachments；
- 已认证的 principal 与 conversation facts；
- 一个 session selector：解析已绑定 session、创建新 session，或请求 owner-authorized resume；
- core id，以及 child/evolver run 可选的显式 pinned revision；
- 可选的不可变 Host route identity；live route adapter 仍是 injected dependency；
- 不可变的 input/output slot selection 与 injected context；
- bootstrap flag 与稳定的 request/idempotency key。

它不包含 `LoadedCore`、`CapabilityFacade`、state/history stores、event logs、provider
clients 或 mutable runner。

`TurnResult` 是 frozen snapshot：

- session、turn、core 与 pinned revision ids；
- terminal outcome；需要暂停并等待用户输入时，outcome 包含 `needs_user`；
- 不可变的 delivery/tool-result summaries；
- agent result 与 durable result reference。

它不得暴露 return 后 dispatch status 仍会变化的 interaction objects。

预期的产品 outcome 会作为返回值提供，而不是以 adapter exception 泄漏：

```text
completed | needs_user | failed | cancelled | lost | indeterminate
```

`turn.started` 前的 validation、authentication 或 admission failure 会抛出 typed
`TurnRejected`，其中只有有界 reason，且不会泄漏跨 owner 的对象是否存在。启动后的
provider、slot 与 effect failure 会经过 sanitization 与 persistence，并以
`TurnResult(outcome="failed")` 返回。显式 cancellation 返回 cancelled result。环境中的
Host coroutine cancellation 会记录 cancelled 或 indeterminate terminal outcome，然后
重新抛出 cancellation。如果 storage 或 invariant failure 使可信 terminal record 无法创建，
会抛出 typed
`TurnInfrastructureError(request_id, durable_result_ref?, outcome="indeterminate")`；
调用方不得据此推断成功，也不得重试 non-idempotent work。

### 顺序与不变量

一次调用拥有以下顺序：

1. 在产生 side effect 前验证并深度冻结 request。
2. 认证 principal，并原子地解析或创建持久 conversation binding。
3. 在第一次 awaitable authored/provider 操作前取得 per-session admission lease。
4. 固定 core revision、capability snapshot、workspace、route 与 trace ids。
5. 持久化 `turn.started`。
6. 要求 EffectRuntime 内部 catalog seam 完成不可变 per-turn definitions 与 resolved
   references。
7. 让 bootstrap 与 input slots 使用同一个 catalog 运行，然后持久化 normalized input。
8. 把该 catalog 的最终 definitions 传给 `ContextManager.prepare()`，再运行 provider/effect
   steps 与 output slots。
9. 提交 terminal turn state 与 durable delivery intents。
10. 返回不可变结果，并在 `finally` 中释放 catalog、route 与 admission resources。

必须满足以下不变量：

- module 内部串行同一 session 的 turn；
- 不同 session 仍可并发，不存在进程级 turn lock；
- session switching 只影响未来 request；
- 每个下游操作都使用 `TurnExecutionContext`，绝不使用 mutable “current runner session”；
- core revision 与 capabilities 不会在 turn 中途变化；
- delivery 使用已捕获的 session/route identity；
- `turn.started` 之后的每个出口都恰好产生一个 terminal state；
- foreground completion 拒绝迟到的 slot/tool write；
- detached work 是独立拥有的 runtime task，不会迟到修改 parent turn。

Transactional runtime store 是 lifecycle source of truth。Admission 会把
request/idempotency key 与 `turn.started`，连同 resolved session binding/lease，一起原子
持久化。Terminal turn state 与 durable delivery/outbox intents 在一个 transaction 中
提交。Event logs、display state 与 live route delivery 都是这些记录的 projection 或
consumer，绝不是相互竞争的 completion authority。

### 错误、取消、重启与性能

- Validation/authority failure 发生在 `turn.started` 之前。
- Provider、slot 与 effect failure 会持久化并返回 failed terminal result，不暴露 adapter
  exception。
- Cancellation 会检查 owner 且保持幂等，持久化 cancelled terminal turn，释放 admission，
  并且不会隐式取消 detached tasks。
- Restart 会让 admission lease 过期或恢复它，并把 orphaned running turn 显式标成
  lost/failed/cancelled；绝不静默重放危险的 provider/effect step。
- Admission lookup 按 key 执行并实际达到 O(1)；清除 idle lock entries；session/task owner
  queries 有索引、有界且支持分页。

当前 `TurnExecutionScope` 只是前身，并非最终 context。Containment runtime 现在用单进程
keyed lock 串行 same-session turn，并把 captured session 贯穿 prompt、IO、slot
history/result、event、artifact 与 delivery 路径。它仍携带 mutable objects，lock 也不具备
restart durability；完整的 principal/revision/route/cancellation contract 仍由后续
`TurnExecution` 实现负责。

## EffectRuntime

### 接口

```text
EffectRuntime.execute(EffectRequest, TurnExecutionContext) -> EffectResult
```

`EffectRequest` 包含：

- 唯一的 call/request id；
- 来自不可变 per-turn catalog 的 opaque resolved effect reference；
- 深度冻结的 arguments；
- model、authored slot/tool 或 Host 等 invocation origin。

Per-turn catalog 同时生成 provider-visible definitions 与 opaque resolved reference。
`execute()` 绝不再次执行全局 name lookup。Capability、workspace、principal、approval、
secret values 与 adapter choice 都不是调用方提供的 request 字段。

### 内部 Catalog Seam

Catalog preparation 是由 `EffectRuntime` module 拥有的真实内部 seam；它不是第二个
registry module。`TurnExecution` 会在 core、principal、revision、capability 与
workspace bindings 固定后使用它：

```text
prepare_catalog(TurnExecutionContext) -> PreparedEffectCatalog
PreparedEffectCatalog.definitions
PreparedEffectCatalog.resolve(provider_tool_name) -> ResolvedEffectRef
PreparedEffectCatalog.close()
```

`prepare_catalog()` 应用 namespace 与 connect policy，执行获批的 MCP
connect/discovery，并冻结 definitions 与 opaque resolved references。
`ContextManager.prepare()` 在 provider I/O 前接收这些最终 definitions。Model loop
只通过该 catalog 解析返回的 tool name，并把其 opaque reference 传给 `execute()`。
Catalog connection/resource 从 `TurnExecution` 的 `finally` 路径关闭。这些操作属于
Host 内部 composition，不向 Agent Core 作者暴露。

`EffectResult` 至少区分：

```text
succeeded | denied | invalid | not_found | failed | timed_out | cancelled | indeterminate
```

它会记录 execution 是否已开始，并提供各自独立有界且脱敏的 model、operator、event 与
durable views。Raw adapter output 只在内部保留。

### 顺序与不变量

每次 builtin、authored 与 MCP invocation 都遵循同一个顺序：

1. 验证 request 与 resolved catalog binding。
2. 强制执行 principal/tool visibility 与 owner scope。
3. 要求 capability snapshot。
4. 运行纯 Host 检查：namespace、workspace/cwd、command、URL/redirect、process、
   environment 与 output policy。
5. 解析 approval。
6. 只绑定显式授权的 secrets/environment。
7. 在 deadline 与 cancellation 下调用选定 adapter。
8. 完成 cleanup、streaming limits、redaction、safe views 与 audit state。

对于 Host-mediated model-triggered effect，在适用的 capability 与 approval check 之前，
不得发生 authored tool import/invocation、subprocess spawn、MCP connect/discovery、file
mutation 或 network effect。这并不宣称能控制已经 import 的 `host_shared` Slot code
直接发起的任意 Python/OS call；该独立风险归 `SlotRuntime` 与可选 isolation 所有。

MCP connection/discovery 是独立的 `mcp.connect:<server>` effect。后续
`mcp.call:<server>` 使用准确的 connection-bound resolved entry；全局 tool-name index
绝不是 dispatch authority。

显式 cancellation request 只有在确认 cleanup 后才返回 `cancelled`；不确定的
process-tree 或 remote cleanup 返回 `indeterminate`。如果 Host coroutine 本身被取消，
module 会先持久化相同 typed outcome，再重新抛出 cancellation，而不是把它转成普通
tool error。External side effect 已发生但尚未 durable confirmation 就 crash，也属于
`indeterminate`。Non-idempotent foreground effect 不会在 restart 后自动重放。
Durable/background effect 返回 Host-work handle，并使用该子系统的 recovery contract。

Output 在读取或 streaming 时就受到限制，而不是等完整加载 file、tree、subprocess
output、MCP result 或 event payload 后再限制。Discovery 具有 per-server timeout、
bounded parallelism、failure backoff 与 lifecycle eviction。

仅仅集中 invocation policy 并不会让 `host_shared` 变成 sandbox。Authored Python 一旦
import，就能使用普通 Python/OS API。可选 subprocess/per-core isolation 是未来在同一
seam 上实现的 adapter。

## ContextManager

### 接口

```text
await ContextManager.prepare(ContextRequest) -> PreparedContext
await ContextManager.observe(UsageObservation) -> None
```

`ContextRequest` 包含：

- 不可变的 `TurnExecutionContext`；
- step id，以及 `ProviderRuntime` 提供的 normalized immutable model limits；
- 冻结的 current-turn messages 与 context contributions；
- 最终 per-turn effect definitions，以便把 schema overhead 纳入预算；
- bootstrap-use flag。

`ProviderRuntime` 拥有 context window、maximum output、tokenizer/estimator identity 与
provider safety margins 等 provider/profile normalization。`ContextManager` 独占根据这些
limits 选择 per-step input/output budget 的职责；调用方不会传入预先计算的 reservation。

History、bootstrap snapshot、summary/cutoff、leases、estimators、persistence 与
summarizer clients 属于实现知识。

`PreparedContext` 是 tagged `ready | overflow` result。Ready result 返回
provider-neutral immutable messages、选定的 output budget、估算 input size 与 hard
budget、opaque decision id，以及有界且不含敏感信息的 decision/layer summary。
Overflow result 不包含 provider request，并给出 typed、bounded recovery reason。

`UsageObservation` 通过 decision id 加 session/turn/step、provider/model、
input/output/cache token buckets、finish reason 与 provider request id 进行关联。
`observe()` 保持幂等，绝不更新环境中的全局 “last usage” record。

`TurnExecution` 会在 response normalization 后、下一次 `prepare()` 或 terminal commit
前调用 `observe()`。Observation 会先 durable append，再推进 calibration state。Typed
observation-write failure 不会导致 provider request 重复，也不会丢弃有效 response：
该 turn 会记录 degraded context telemetry，跳过尚未提交的 calibration；下一次
`prepare()` 使用 conservative estimate，如果无法确认安全则返回 `overflow`。

### 顺序与不变量

`prepare()`：

1. 通过 `TurnExecutionContext.session_id` 读取 history。
2. 选择 output reservation，并根据 normalized model limits、schema overhead 与 safety
   margin 计算 input budget。
3. 确定性地组装 layers。
4. 在 summarization 前低成本裁剪旧 tool/media results。
5. 估算完整 provider request。
6. 必要时取得 session compaction lease，重新验证 snapshot，执行 compaction，并原子
   提交 summary 与 cutoff。
7. 保留 current input、因果关联的 assistant/tool groups、受保护的 head/tail 与
   reference-only summary 语义。
8. 在 provider I/O 之前返回有界 context 或 typed overflow result。

Summary failure 会使用确定性的有界 fallback；若原 context 仍装得下，则保持原样。
Cancellation 不提交任何 partial summary/cutoff，并释放 lease。Lease/cooldown state 可在
restart 后恢复。Model switch 会使旧 calibration 失效。普通 event 绝不持久化完整 prompt；
显式 debug output 也必须有界并脱敏。

如果另一个 worker 持有 compaction lease，`prepare()` 只等待有界时间，然后重新读取已
提交的 summary/cutoff。如果仍没有可用 commit，它会应用确定性的低成本 fallback 或返回
`overflow`；不会启动第二个 summarizer，也不会无限等待。

`prepare()` 基于有界 retained window 工作，不会反复加载完整 transcript。
`observe()` 实际达到 O(1)。

当前 `ContextAssembler` 控制 layer order，而 `PromptContextRuntime` 读取 mutable runner
session state，`SessionCompactionRuntime` 则拥有独立的 manual flow。这些都是要折叠到
该接口之后的内部实现，不是额外的外部 owner。

## ChannelInbox

### 接口与词汇

```text
ChannelInbox.accept(InboundEnvelope) -> InboxReceipt
ChannelInbox.claim() -> ClaimedInbound | None
ChannelInbox.complete(ClaimedInbound, InboundResult) -> CompletionDecision
ChannelInbox.fail(ClaimedInbound, InboundFailure) -> RetryDecision
```

`InboundEnvelope` 包含 channel-instance id、稳定 platform event key、kind、canonical
conversation key、authenticated principal facts、received time、有界 payload/payload
reference、artifact references 与可选 source checkpoint。Platform adapter 只提供事实；
它绝不构造 operator/admin `PrincipalScope`。

`InboxReceipt` 返回 durable inbound id 与 `accepted | duplicate` disposition。Duplicate
会返回相同的 durable identity。

`ClaimedInbound` 包含 envelope、claim token、attempt、lease expiry，以及从 durable
inbound id 预留的稳定 turn-request/idempotency id。Reconciliation 找到 existing
turn/result 时，也会包含其 correlation。`InboundResult` 支持 turn completion、command
completion、ignored input 与 cancellation；并非每个 inbound 都创建 model turn。
`InboundFailure` 有明确类型且已脱敏。

### 顺序与不变量

1. Body limits、signature/token、allowlist 与 minimum parsing 在 `accept()` 前运行。
2. 对单个 event，durable envelope 与 dedup identity 原子提交。内部 batch/checkpoint
   operation 会在一个 transaction 中提交所有新 envelope、dedup key 与 source
   checkpoint；public `accept()` 是其 single-envelope equivalent。
3. Transport acknowledgement 只会在 `accepted` 或 `duplicate` 之后发生：push 返回
   2xx/202，Email 标记 `Seen`，polling 才能推进 cursor。
4. Store failure 返回 5xx，或保留旧 cursor/unread message。
5. `claim()` 使用 lease/token；只有当前 claimant 能 complete/fail。
6. Worker 会在启动 model turn 前持久预留稳定 request/idempotency id。Claim 前 crash
   可恢复；turn 中 crash 会先 reconcile 该 id 与所有 existing turn，再创建另一个 turn。
7. 用户请求的 turn cancellation 是 terminal，不会重放原始 message。
8. Worker shutdown 若没有 business result，会释放 lease 或让其过期，以执行 transient retry。
9. Attempts 与 payloads 有界。Authentication、signature、allowlist、parse 与 body-limit
   failure 发生在 `accept()` 前，并返回 transport 4xx/413，不创建 inbox/DLQ row。
   已认证但无法处理或反复失败的 poison event 才会变成 terminal reject/dead-letter record。
10. Inbox completion 不表示 outbound delivery 已成功；outbox 与 `DeliveryRuntime` 继续
    拥有该职责。

Retry 使用带 jitter 的有界 exponential backoff，并最终进入脱敏 DLQ。Operator requeue
是显式操作，不存在无限 automatic replay。

对于相同 claim token 与 terminal payload，`complete()` 和 `fail()` 都保持幂等。
`CompletionDecision` 与 `RetryDecision` 除正常 complete/retry/dead-letter outcome 外，
还包含 typed `stale_claim | already_terminal | conflict` disposition；它们绝不覆盖当前
claimant，也不会静默改变 terminal record。

Dedup 使用 indexed unique `(channel_instance_id, platform_event_key)` key。Due claim 使用
indexed next-attempt/lease 字段、有界 claim batch，并保证不同 channel instance 间的
fairness，而不是 full-table scan 或让单一 noisy channel 独占 worker。Payload/artifact
retention 与 dedup tombstone 分别设定边界。Dedup replay horizon 至少覆盖所支持 transport
的最长 retry/replay window；payload pruning 不得使旧 platform event 在该 horizon 内再次
被接受，active/DLQ evidence 在仍可操作时也不得被清理。

Durable stream checkpoints 是 `ChannelInbox` 内部 seam。只有 batch 中每个 event 都持久
accepted 后才推进 batch cursor；replay 会被 unique dedup key 吸收。

Production adapter 由 SQLite 支持。严格的 in-memory adapter 会运行同一套
claim/lease/idempotency contract suite。Platform transports 仍是 protocol adapters，
不是另一套 inbox owners。

## 外部与内部 Seams

| Concern | 外部 Host seam | 内部实现 seams/adapters |
| --- | --- | --- |
| Turn lifecycle | `TurnExecution` | admission、persistence、provider loop、slot、IO、delivery，以及绑定到单一 context 的 test hosts |
| Authority | owner interfaces 上的 `PrincipalScope` | authenticated channel/operator/system/delegated-agent resolvers |
| Effects | `EffectRuntime` | catalog prepare/resolve/close、builtin/authored/MCP adapters、approval provider、process executor、URL policy、secret redactor、output views |
| Context | `ContextManager` | history store、estimator、compaction lease、summarizer、fallback、telemetry |
| Inbound channels | `ChannelInbox` | SQLite/in-memory inbox、source checkpoint store、platform envelope adapters |

Production 与 test adapters 使这些内部 seam 有存在价值。不要向 Agent Core 作者暴露
test-only adapter，也不要为尚未变化的 dependency 创建假想 public port。

## Primary Finding Owners

每条 audit finding 都只有一个 primary owner。其他 module 可以提供内部 helper，但不会
成为第二个 policy owner。Finding ID 是 contributor/regression label，不是 public runtime
identifier；使用该 ID 查找其 probe 或 permanent test，以及 implementation history。

| Primary owner module | Findings |
| --- | --- |
| `TurnExecution` | SES-01 |
| `EffectRuntime` | SEC-01, TOOL-01, TOOL-03, ENV-01, MCP-01, MCP-02, MCP-03, PROC-01, NET-01, IO-01, TOOL-02 |
| `ApprovalRuntime` | AUTH-01 |
| `SessionRuntime` | SES-02 |
| `StateRuntime` | STATE-01 |
| `RuntimeStore` | STORE-01 |
| `RuntimeControlPlane` | TASK-01 |
| `RuntimeTaskWorker` | TASK-02, TASK-03, LOG-01 |
| `SchedulerRuntime` | SCHED-01 |
| `ProviderRuntime` | PROV-01, PROV-02 |
| `ContextManager` | CTX-01 |
| `SlotRuntime` | SLOT-01, MOD-01 |
| `ChannelInbox` | CH-01, CH-02, CH-03 |
| `ChannelSupervisor` | CH-04 |
| `DiagnosticsRuntime` | CLI-01, CLI-02, CLI-03, SETUP-01 |
| `TuiLauncher` | TUI-01 |
| `OperatorGatewayRuntime` | UI-01 |
| `OperatorTui` | UI-02 |
| `ManagedUpdateRuntime` | UPDATE-01 |
| Webhook transport adapter | HTTP-01 |
| `RuntimeSecurityPolicy` | SEC-02 |

## 迁移与删除规则

- 新契约取代旧的 shallow forwarding path；不得在其旁边增加永久的第二条路径。
- `Runner*Host` adapter 只有绑定到一个不可变 execution context 时才能保留为内部实现。
  Generic runner back-reference 不是外部 seam。
- Registry definitions、operator display、provider schemas 与 execution 使用同一个 resolved
  effect entry。
- 当前 `InteractionInbound` 会成为 inbox worker 与 TurnExecution 之间的 compatibility
  DTO，而不是 durable inbox schema。
- JSON-backed `StateStore` 只用于 containment。最终 production state semantics 归
  `StateRuntime` 所有，并在 transactional `RuntimeStore` 上实现；不会永久保留
  JSON/SQLite 双 owner。当前 containment 会在单进程内按 resolved state path 串行，
  使用 content-hash CAS 做内部 stale-writer detection，并通过 atomic file 与
  prepared/committed recovery journal 一起发布 state 和 proposal audit。POSIX state
  path 使用显式 private mode bit；Windows 遵循平台 ACL semantics。该 containment
  不会声称支持 cross-process locking，也不会把两个分开的 authored `get()` 与 `set()`
  变成 transaction。现有 JSON document 不需要 schema rewrite；后续 migration 会把
  它们导入 `StateRuntime`，然后退役 JSON writer。
- 除非 owner table 明确移动 ownership，现有 `DeliveryRuntime`、`SessionRuntime`、
  `RuntimeStore`、task worker、scheduler、provider 与 slot modules 仍保留各自专门职责。
- Breaking cleanup 可以删除 private forwarding methods 与旧 internal layout。
  Compatibility shim 必须有显式 migration decision。

## 参考项目边界

Hermes 是只读的机制参考，不是目标架构或代码来源。可借鉴的思想包括
admission-before-await、process-tree lifecycle、context budgeting、retry vocabulary 与
cursor/dedup test scenarios。

不要复制其 gateway god-file、public context plugin engine、把大型 regex policy 当作
sandbox 的做法、runtime lazy dependency installation 或宽泛的 adapter compatibility
surface。本契约没有复制任何 Hermes 代码。
