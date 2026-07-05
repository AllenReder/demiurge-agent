---
title: Delivery and History
description: Reference for authored delivery requests and session history writes.
---

# Delivery and History

Authored output modules and authored tools request delivery through host-owned
SDK clients. The module does not write channel messages or session records
directly.

The host turns delivery requests into:

- session messages
- runtime events
- artifacts
- TUI or gateway deliveries routed by session
- model-visible or model-hidden history

## Delivery Calls

Common output methods:

```python
ctx.output.send_text("Done")
ctx.output.progress("Working...")
ctx.output.notice("Skipped optional step")
ctx.output.send_image("chart.png", caption="Trend chart", history_text="Sent a trend chart.")
ctx.output.send_audio("voice.mp3", media_type="audio/mpeg", history_policy="transient")
ctx.output.send_video("clip.mp4", summary="Demo clip", history_text="Sent a demo clip.")
ctx.output.send_file("report.pdf", summary="Report PDF", history_text="Sent a report.")
```

Artifact paths must be inside the workspace or the session artifact root.

## History Policies

Valid delivery history policies are:

| Policy | Behavior |
| --- | --- |
| `persist` | Write assistant history and include it in later model context. |
| `model_hidden` | Write assistant history but hide it from later model context. |
| `transient` | Deliver or queue live output without writing assistant history. |

`write_history=True` maps to `persist`. `write_history=False` maps to
`transient` unless `history_policy` is explicitly supplied.

## Visibility

| Shape | Meaning |
| --- | --- |
| `visible=True, history_policy="persist"` | User-visible assistant history and model-visible context. |
| `visible=True, history_policy="model_hidden"` | User-visible history that later model context does not see. |
| `visible=True, history_policy="transient"` | Live delivery only. |
| `visible=False, history_policy="persist"` | Hidden assistant history for later model context. |
| `visible=False, history_policy="model_hidden"` | Hidden durable history. |

`visible=False` with transient delivery has no user or history effect and is
rejected.

## Text and Artifact History

Text-only sends can infer history text:

```python
ctx.output.send_text(ctx.output.response_text)
```

Non-text sends that write history need `history_text`:

```python
ctx.output.send_image(
    "chart.png",
    caption="Trend chart",
    history_text="Sent a trend chart.",
)
```

If a non-text delivery writes history without `history_text`, the host rejects
the request because later context would have no usable text representation.

## Slot Defaults

`slot.yaml` can set:

```yaml
history_policy: persist
```

The delivery request can override that default with `history_policy=...`.

Serial output slots default to writing history. Parallel output slots and
background output paths cannot write session history.

Input slots default their `ctx.input.send_*` history writes to transient, since
input slots run before the assistant response.

## Delivery Timing

Author-facing delivery timing options are not part of the current SDK. `send_*`
submits an immediate host delivery request. The host owns session route lookup,
artifact storage, dispatch status, retry/degradation events, and final session
records.

## Dispatch Status

Every live delivery has a required `session_id`. The runtime dispatches ordinary
delivery through the active route bound to that session. `channel` remains
adapter metadata and does not determine route ownership.

`InteractionItem.dispatch_status` uses:

| Status | Meaning |
| --- | --- |
| `pending` | The item has not been scheduled or delivered yet. |
| `scheduled` | The item is queued for host-managed dispatch. |
| `delivered` | A route for the session accepted the outbound. |
| `failed` | A route existed, but adapter delivery raised an error. |
| `unrouted` | No active route exists for the outbound session. |

Durable outbox rows use `queued`, `sending`, `sent`, `failed`, `unknown`, and
`unrouted`. `unknown` is reserved for recovery after a crash between claiming
delivery work and recording a platform result.

Child agent sessions do not inherit the parent's route. Their live deliveries
are sent only to a route explicitly bound for the child session; otherwise they
become `unrouted`.

## Channel Fallback

Every delivery has text fallback where possible. Some channels may render only
text for a media delivery. The host records degraded delivery events when a
channel cannot render the richer block type.

## Boundary

Authored modules request delivery. The host owns persistence, artifacts, session
route lookup, channel dispatch, delivery status, and history visibility.
