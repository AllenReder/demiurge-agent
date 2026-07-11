---
title: Runtime Control Plane v2
description: Host-owned action、task、event、projection 和 Agent Slot v2 设计。
---

# Runtime Control Plane v2

本文记录破坏性 runtime 重构的实现契约。Host 拥有 harness。Agent Core 拥有
`agent/` 下的 authored files。

## Module Shape

新的 deep modules 是：

- `RuntimeStore`：SQLite event store 和 projection surface。
- `RuntimeControlPlane`：host-owned detached task seam。
- `SessionRuntime`：session admission 和 session/turn/message projections。
- `TurnEngine`：一个 Agent Core foreground turn 的 provider/tool loop。
- `SlotRuntime`：按 phase 执行 authored slot callable。

detached-work task ledger 模型是：

```text
TaskSpec -> Task -> Event -> Projection
```

可作为 task 观察的 detached host work 通过
`RuntimeControlPlane.submit_task()` 进入。当前 task-ledger kinds 是
`agent.spawn`、`terminal.exec`、`evolver.run` 和 `schedule.fire`。Foreground
Agent Core turn 不会成为 task row；它们通过 `SessionRuntime` 投影为 turns 和
messages。Delivery、approval、tool-call、MCP、state 和 artifact facts 应该使用
自己的 projections 或 runtime events，而不是伪装成 task submission。

## Storage

runtime database 是 `~/.demiurge/runtime/runtime.sqlite3`。它使用 Python
stdlib `sqlite3` 和 WAL mode。旧的 JSON/JSONL session、scheduler 和
background-task state 不迁移。restart 后发现的 in-progress subprocess work
必须标记为 `lost` 或 `interrupted`；host 不能在 crash 后 replay dangerous
effects。

## Agent Slot Layout

每个 bootstrap、input 和 output slot 在自己的 slot directory 中拥有一个
`slot.yaml` manifest。`agent/pipelines.yaml` 是唯一的 phase ordering graph：

```yaml
schema_version: 1
bootstrap:
  serial: []
input:
  serial: [base_input]
  parallel: []
output:
  serial: [base_output]
  parallel: []
```

Slot code 和 metadata 保存在 typed folders 中：

```text
agent/bootstrap/<slot_id>/module.py
agent/bootstrap/<slot_id>/slot.yaml
agent/input/<slot_id>/module.py
agent/input/<slot_id>/slot.yaml
agent/output/<slot_id>/module.py
agent/output/<slot_id>/slot.yaml
```

`base_input` 和 `base_output` 是普通可编辑 seed slots。Host 不把它们当作
built-ins，loader 也不要求这些 id 存在。

Input slots 构建当前 model context。`ctx.input.raw_text` 是 read-only。
Slots 使用 `ctx.input.add_context(text, role="user"|"system",
write_history=...)`。Output slots 读取 `ctx.output.response_text` 并使用
`ctx.output.send_*`。面向作者的 delivery timing 参数已移除：每次 send 都会
立即记录一个 delivery intent。

Serial slots 可以影响主流程。Parallel slots 是 non-blocking background
side-effect lanes，不能修改 prompt、assistant response 或 session history。

## Current Implementation Slice

runtime store 现在是 sessions、turns、messages、foreground tool-call records、
task status、task logs、scheduler instances、artifacts、delivery outbox rows、
runtime work items 和 unique channel conversation bindings 的 hot-path source
of truth。Foreground tool-call records 由当前 `turn_id` 和 model-loop
`step_id` 标识；它们不是 task facts。旧安装留下的 JSON session 和 scheduler
files 可能仍在磁盘上，但 runtime code 不读取、不迁移，也不 dual-write 它们。

`RuntimeTaskWorker` 是 active subprocess、terminal、evolver 和 child-agent
work 的 live worker。它只在内存中保存 non-durable process handles、cancel
callbacks 和 live completion subscribers。Public task reads、lists、logs、
waits、cancellation results 和 pending completion notifications 都从
`RuntimeControlPlane` / SQLite projections 与 runtime events 重建。

`BackgroundWorkRuntime` 跟踪 parallel slots 和 delivery dispatch 创建的
in-process background coroutines。它把这些 local tasks 与 durable
`RuntimeTaskWorker` 组合起来提供 drain 和 active-count 行为；foreground runner
不再拥有单独的 background-task ledger。

`DeliveryRuntime` claim 匹配的 durable work item 后，通过 session-scoped interaction
router dispatch queued delivery intents。Outbox lifecycle 是
`queued -> sending -> sent/failed/unknown/unrouted`。`unrouted` 表示该 delivery
session 没有绑定 live route；`failed` 表示 route 存在但 adapter delivery 抛错。
Delivery failure 可以用显式 failure history text 更新此前已持久化的 history row，
但 retries 不能重写原始 history body。

`SessionTurnStepRunner` 现在委托：

- session creation、update、turn lifecycle 和 message persistence 给
  `SessionRuntime`；
- foreground turn admission，包括 session/core resolution、route binding、
  revision/capability pinning 与 turn begin，给 `TurnAdmissionRuntime`；
- authored input -> model/tool -> output execution、captured-route context、
  bootstrap、owner-checked cancellation、delivery drain 与最终 cleanup 给
  `TurnExecution`；
- foreground input record、assistant output record、display turn、completion 与
  interruption 给 `TurnPersistenceRuntime`；
- provider/tool loop execution 给 `TurnEngine`；
- authored bootstrap/input/output slot callable loading 和 invocation 给
  `SlotRuntime`。

面向 model 的 delegation tools 是：

- `delegate_task(goal, core_id=None, context_mode="isolated",
  notify_policy="return_to_parent", tool_policy=None, max_depth=None)`；
- `task_list(kind=None)`，限定当前 session；
- `task_status(task_id, view="model")`；
- `task_control(task_id, command="cancel")`；
- `yield_until(task_id, timeout_seconds=30)`。

`delegate_task` 当前支持 `isolated` 和 `fork` context modes，执行默认 depth 和
child-count limits，并在 visible-tool construction 和 dispatch 阶段应用 child
`tool_policy` filters。`notify_policy` 只接受 `return_to_parent` 和 `silent`；
前者会发出 completion event，后者会抑制它。Child output 默认作为 parent 的
evidence。

Foreground turn 不能通过 control-plane projection 按 task id 读取。它们通过
session、turn、message、event-log 和 runtime-event projections 保持可追踪。
面向 model 的 task tools 只操作 detached background task kinds，所以普通 turn
不会出现在 `task_list` 中，也不支持 `task_status`、`task_control` 或
`yield_until`。
