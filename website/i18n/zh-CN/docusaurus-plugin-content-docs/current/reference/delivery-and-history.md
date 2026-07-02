---
title: Delivery 和 History
description: Authored delivery requests 和 session history writes 的参考。
---

# Delivery 和 History

Authored output modules 和 authored tools 通过 host-owned SDK clients 请求 delivery。Module 不会直接写入 channel messages 或 session records。

Host 会把 delivery requests 转换为：

- session messages
- runtime events
- artifacts
- TUI 或 gateway deliveries
- model-visible 或 model-hidden history

## Delivery Calls

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

Artifact paths 必须位于 workspace 或 session artifact root 内。

## History Policies

有效的 delivery history policies 是：

| Policy | Behavior |
| --- | --- |
| `persist` | 写入 assistant history，并包含在后续 model context 中。 |
| `model_hidden` | 写入 assistant history，但从后续 model context 中隐藏。 |
| `transient` | 发送或排队 live output，但不写入 assistant history。 |

`write_history=True` 映射到 `persist`。除非显式提供 `history_policy`，否则 `write_history=False` 映射到 `transient`。

## Visibility

| Shape | Meaning |
| --- | --- |
| `visible=True, history_policy="persist"` | User-visible assistant history 和 model-visible context。 |
| `visible=True, history_policy="model_hidden"` | Later model context 看不到的 user-visible history。 |
| `visible=True, history_policy="transient"` | 仅 live delivery。 |
| `visible=False, history_policy="persist"` | 供后续 model context 使用的 hidden assistant history。 |
| `visible=False, history_policy="model_hidden"` | Hidden durable history。 |

`visible=False` 加 transient delivery 没有 user 或 history effect，会被拒绝。

## Text 和 Artifact History

Text-only sends 可以推断 history text：

```python
ctx.output.send_text(ctx.output.response_text)
```

会写入 history 的 non-text sends 需要 `history_text`：

```python
ctx.output.send_image(
    "chart.png",
    caption="Trend chart",
    history_text="Sent a trend chart.",
)
```

如果 non-text delivery 在没有 `history_text` 的情况下写入 history，host 会拒绝该请求，因为后续 context 没有可用的文本表示。

## Slot Defaults

`slot.yaml` 可以设置：

```yaml
history_policy: persist
```

Delivery request 可以用 `history_policy=...` 覆盖这个 default。

Serial output slots 默认会写入 history。Parallel output slots 和 background output paths 不能写入 session history。

Input slots 的 `ctx.input.send_*` history writes 默认使用 transient，因为 input slots 在 assistant response 之前运行。

## Delivery Timing

当前 SDK 不包含面向 author 的 delivery timing options。`send_*` 会提交即时 host delivery request。Host 拥有 channel routing、artifact storage、dispatch status、retry/degradation events 和 final session records。

## Channel Fallback

每个 delivery 都会尽可能提供 text fallback。某些 channels 可能只为 media delivery 渲染文本。当 channel 无法渲染更丰富的 block type 时，host 会记录 degraded delivery events。

## 边界

Authored modules 请求 delivery。Host 拥有 persistence、artifacts、route context、channel dispatch、delivery status 和 history visibility。
