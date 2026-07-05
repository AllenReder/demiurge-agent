---
title: Delivery 和 History
description: Authored delivery requests 和 session history writes 的参考。
---

# Delivery 和 History

Authored input slots、output slots 和 authored tools 通过 host-owned SDK
clients 请求 delivery。Authored code 不会直接写入 channel messages 或 session
records。

Host 会把 delivery requests 转换为：

- session messages
- runtime events
- artifacts
- 按 session route 的 TUI 或 gateway deliveries
- model-visible 或 model-hidden history

完整 slot `ctx` 对象见 [Slot Context SDK](slot-context-sdk.md)。

## Delivery Methods

常见 output methods：

```python
ctx.output.send_text("Done")
ctx.output.progress("Working...")
ctx.output.notice("Skipped optional step")
ctx.output.send_image("chart.png", caption="Trend chart", history_text="Sent a trend chart.")
ctx.output.send_audio("voice.mp3", media_type="audio/mpeg", history_policy="transient")
ctx.output.send_video("clip.mp4", summary="Demo clip", history_text="Sent a demo clip.")
ctx.output.send_file("report.pdf", summary="Report PDF", history_text="Sent a report.")
```

`ctx.input` 也暴露同一组 delivery methods，可用于 input phase status 或
artifacts。Input deliveries 默认使用 transient history，因为 input slots 在
assistant response 之前运行。

## `send_text`

```python
ctx.output.send_text(
    text,
    write_history=None,
    history_policy=None,
    visible=True,
    history_text=None,
    failure_history_text=None,
    delivery_metadata=None,
)
```

| Parameter | 含义 |
| --- | --- |
| `text` | 要发送的 text block。 |
| `write_history` | `True` 映射到 `persist`；`False` 映射到 `transient`，除非显式提供 `history_policy`。 |
| `history_policy` | `persist`、`model_hidden` 或 `transient`。 |
| `visible` | Delivery 是否对用户可见。 |
| `history_text` | 写入 history 的文本；默认是 `text`。 |
| `failure_history_text` | 可供失败历史处理使用的文本。 |
| `delivery_metadata` | 附加到 host delivery request 的 metadata。 |

`send_text(...)` 返回带稳定 `delivery_id` 的 `DeliveryHandle`。

## Artifact Sends

`send_image`、`send_audio`、`send_video` 和 `send_file` 共享同一个参数形状：

```python
ctx.output.send_file(
    source,
    caption=None,
    media_type=None,
    summary=None,
    artifact_metadata=None,
    write_history=None,
    history_policy=None,
    visible=True,
    history_text=None,
    failure_history_text=None,
    delivery_metadata=None,
)
```

| Parameter | 含义 |
| --- | --- |
| `source` | Workspace/session path、URL 或 `ArtifactRef`。 |
| `caption` | 随 artifact block 渲染的文本。 |
| `media_type` | MIME type hint，例如 `audio/mpeg` 或 `application/pdf`。 |
| `summary` | Host 存储的 artifact summary。 |
| `artifact_metadata` | 随 artifact 存储的 metadata。 |
| `write_history`, `history_policy`, `visible`, `history_text`, `failure_history_text`, `delivery_metadata` | 与 `send_text` 相同的 controls。 |

本地 artifact paths 必须位于解析后的 workspace 或当前 session artifact root 内。
URL 可以作为 artifact source。不能把 raw artifact dict 传给
`send_image/audio/video/file`；请使用 `summary=...` 和 `artifact_metadata=...`，
或传 `ArtifactRef`。

会写入 history 的 non-text sends 应该提供 `history_text`：

```python
ctx.output.send_image(
    "chart.png",
    caption="Trend chart",
    history_text="Sent a trend chart.",
)
```

如果 non-text delivery 写入 history 但没有可用的 history text，后续 model
context 就没有该 delivery 的文本表示。

## Status Sends

```python
ctx.output.progress("Working...")
ctx.output.notice("Skipped optional step")
```

| Method | 含义 |
| --- | --- |
| `progress(text, visible=True, delivery_metadata=None)` | 发送 transient progress。 |
| `notice(text, visible=True, delivery_metadata=None)` | 发送 transient notice。 |

`progress` 和 `notice` 总是使用 transient history，不接受 `history_policy`。

## Low-Level `send`

`ctx.input.send(...)` 和 `ctx.output.send(...)` 接受一个 content block、字符串、
兼容 `ContentBlock` 的 mapping，或这些值的列表。大多数 slot code 应优先使用上面的
typed helpers。

有效 content block types 是 `text`、`image`、`audio`、`video`、`file` 和
`control`。有效 delivery kinds 是 `message`、`progress` 和 `notice`。

## History Policies

有效 delivery history policies：

| Policy | Behavior |
| --- | --- |
| `persist` | 写入 assistant history，并包含在后续 model context 中。 |
| `model_hidden` | 写入 assistant history，但从后续 model context 中隐藏。 |
| `transient` | 发送 output，但不写入 assistant history。 |

`write_history=True` 映射到 `persist`。除非显式提供 `history_policy`，否则
`write_history=False` 映射到 `transient`。

## Visibility

| Shape | Meaning |
| --- | --- |
| `visible=True, history_policy="persist"` | User-visible assistant history 和 model-visible context。 |
| `visible=True, history_policy="model_hidden"` | Later model context 看不到的 user-visible history。 |
| `visible=True, history_policy="transient"` | 仅 delivery。 |
| `visible=False, history_policy="persist"` | 供后续 model context 使用的 hidden assistant history。 |
| `visible=False, history_policy="model_hidden"` | Hidden durable history。 |

`visible=False` 加 transient delivery 没有 user 或 history effect，会被拒绝。

## Slot Defaults 和 Parallel 限制

`slot.yaml` 可以设置：

```yaml
history_policy: persist
```

Delivery request 可以用 `history_policy=...` 覆盖这个 default。

Serial output slots 默认会写入 history。Parallel output slots 和 background output
paths 不能写入 session history。如果 parallel output slot 请求 non-transient
history policy，runtime 会抛出 `RuntimeError`。

Input slots 的 `ctx.input.send_*` history writes 默认使用 transient，因为 input
slots 在 assistant response 之前运行。

## Delivery Timing

当前 authored SDK 不暴露 `live` 或 delayed-delivery 参数。`send_*`、`progress`
和 `notice` 会提交即时 host delivery requests。Host 拥有 session route lookup、
artifact storage、dispatch status、retry/degradation events 和 final session
records。

## Dispatch Status

每个 live delivery 都有必填 `session_id`。Runtime 通过绑定到该 session 的 active
route dispatch 普通 delivery。`channel` 仍是 adapter metadata，不决定 route
ownership。

`InteractionItem.dispatch_status` 使用：

| Status | Meaning |
| --- | --- |
| `pending` | Item 还没有被 scheduled 或 delivered。 |
| `scheduled` | Item 已排队等待 host-managed dispatch。 |
| `delivered` | Session 的 route 接受了 outbound。 |
| `failed` | Route 存在，但 adapter delivery 抛错。 |
| `unrouted` | Outbound session 没有 active route。 |

Durable outbox rows 使用 `queued`、`sending`、`sent`、`failed`、`unknown` 和
`unrouted`。`unknown` 只用于在 claim delivery work 后、记录平台结果前发生 crash
的 recovery。

Child agent sessions 不继承 parent route。它们的 deliveries 只发送到显式绑定
child session 的 route；否则会变成 `unrouted`。

## Channel Fallback

每个 delivery 都会尽可能提供 text fallback。某些 channels 可能只为 media delivery
渲染文本。当 channel 无法渲染更丰富的 block type 时，host 会记录 degraded
delivery events。

## 边界

Authored modules 请求 delivery。Host 拥有 persistence、artifacts、session route
lookup、channel dispatch、delivery status 和 history visibility。
