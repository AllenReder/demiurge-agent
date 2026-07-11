---
title: Operator Gateway
description: 本地 TUI/dashboard gateway runtime 的 contributor 说明。
---

# Operator Gateway

`OperatorGatewayRuntime` 是本地 operator client（例如 TUI 与未来 dashboard surface）
使用的 Python-side product gateway。它不是 messaging channel。

## Responsibilities

Operator gateway 拥有本地 product state 与 control-plane view：

- local operator client 的 session context；
- prompt 与 approval pending state；
- local operator command 的 slash command routing；
- status、history 与 host-work projection；
- local app process 的 scheduler lifecycle；
- 通过 `ConversationLifecycleRuntime` 实现 busy、queue 与 interrupt handling；
- active operator session 的 interaction route binding。

NDJSON launcher 会直接实例化 `OperatorGatewayRuntime`。不存在 compatibility bridge
class 或 legacy TUI protocol facade。

## Event Shape

Operator client 应优先使用 product event 更新 UI state：

- `operator.ready`
- `operator.status`
- `operator.history`
- `operator.work.updated`
- `operator.prompt.opened`
- `operator.approval.opened`
- `operator.error`
- `operator.message`
- `operator.deliver`
- `operator.shutdown`

TUI reducer 只消费 `operator.*` frame。Gateway 下层仍与 messaging channel 共用
`InteractionInbound` 与 `InteractionOutbound` object，但这些名称不是 operator wire
protocol。

## Initialize Identity Handshake

Launcher 默认使用 tracked packaged bundle。Ignored source-checkout
`ui-tui/dist/entry.js` 只有在 `DEMIURGE_TUI_DEV=1` 时才会使用；如果本地安装了 `tsx`，
development mode 也可以运行 `src/entry.tsx`。

第一条 RPC 是携带 `protocol_version` 与 `build_stamp` 的 `operator.initialize`。Python
entrypoint 会在调用 `OperatorGatewayRuntime.initialize()` 前校验两者，再在 result 中返回
Host identity。TUI 必须再次校验该 response，才能把 initialize 视为成功。Mismatch 会返回
RPC code `protocol_mismatch` 并以 exit code 2 结束，因此 stale bundle 不会表现成正常
shutdown。

## Process Exit Contract

NDJSON entrypoint 把进程状态作为 operator protocol 的一部分：

| Exit code | 含义 |
| --- | --- |
| `0` | 已完成身份校验的 client 通过 RPC、`/exit` 或 `/quit` 显式请求 shutdown。 |
| `1` | Host startup 失败，或 client stream 在没有显式 shutdown 时结束。 |
| `2` | CLI 参数解析、gateway 配置或 initialize identity handshake 失败。 |

Host 创建或 `OperatorGatewayRuntime.initialize()` 失败时，stdout 会先收到一个
`source: gateway_startup` 的 `operator.error` event，然后进程以 code 1 退出。Client
不得把这个 lifecycle 转换为 `operator.shutdown`。Protocol/build mismatch 继续使用结构化 RPC code
`protocol_mismatch` 与 process exit 2。Initialize 后失去 stdin 同样属于异常；只有显式且
已校验的 shutdown request 才是 zero-exit lifecycle。`/exit` 与 `/quit` operator
command 会调用和 `operator.shutdown` RPC 相同的 shutdown path。
Malformed launcher configuration 会先发送带 `source: gateway_config` 与
`code: config_error` 的 `operator.error`，再以 code 2 退出；payload 不会回显原始配置值。

Wire contract 变化时，应同步更新 `demiurge/ui_gateway/protocol.py` 与
`ui-tui/src/gateway/protocol.ts`，然后 rebuild，并逐字节比较
`ui-tui/dist/entry.js` 与 `demiurge/ui/tui_dist/entry.js`。

## Boundary With Channels

Messaging channel 拥有 external platform concern：allowlist、remote user/thread routing、
webhook 或 polling lifecycle、platform delivery 与 `run_forever()`。

Operator gateway 拥有 local control concern：session、runtime status、task、package、
schedule、approval、prompt 与 host-work observability。它可以使用 `InteractionInbound` 与
`InteractionOutbound`，从而与 channel 共用 turn entry 和 delivery object，但 TUI/dashboard
不被建模为 `Channel`。

## Long Commands

NDJSON gateway entrypoint 会把 `/doctor`、`/packages`、`/evolve`、`/rollback` 与
`/compact` 等长时间 operator command 与 RPC read loop 隔离。这样在慢命令运行时，prompt
reply、approval reply 与 interrupt 仍能保持响应。
