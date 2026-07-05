---
title: Delivery and History
description: Reference for authored delivery requests and session history writes.
---

# Delivery and History

Authored input slots, output slots, and authored tools request delivery through
host-owned SDK clients. Authored code does not write channel messages or
session records directly.

The host turns delivery requests into:

- session messages
- runtime events
- artifacts
- TUI or gateway deliveries routed by session
- model-visible or model-hidden history

For the full slot `ctx` object, see [Slot Context SDK](slot-context-sdk.md).

## Delivery Methods

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

`ctx.input` exposes the same delivery methods for input-phase status or
artifacts. Input deliveries default to transient history because input slots run
before the assistant response.

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

| Parameter | Meaning |
| --- | --- |
| `text` | Text block to deliver. |
| `write_history` | `True` maps to `persist`; `False` maps to `transient` unless `history_policy` is supplied. |
| `history_policy` | One of `persist`, `model_hidden`, or `transient`. |
| `visible` | Whether the delivery is user-visible. |
| `history_text` | Text written to history; defaults to `text`. |
| `failure_history_text` | Text available for failure history handling. |
| `delivery_metadata` | Extra metadata attached to the host delivery request. |

`send_text(...)` returns a `DeliveryHandle` with a stable `delivery_id`.

## Artifact Sends

`send_image`, `send_audio`, `send_video`, and `send_file` share one parameter
shape:

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

| Parameter | Meaning |
| --- | --- |
| `source` | Workspace/session path, URL, or `ArtifactRef`. |
| `caption` | Text rendered with the artifact block. |
| `media_type` | MIME type hint such as `audio/mpeg` or `application/pdf`. |
| `summary` | Artifact summary stored by the host. |
| `artifact_metadata` | Metadata stored with the artifact. |
| `write_history`, `history_policy`, `visible`, `history_text`, `failure_history_text`, `delivery_metadata` | Same controls as `send_text`. |

Local artifact paths must be inside the resolved workspace or the current
session artifact root. URLs are accepted as artifact sources. Passing raw
artifact dictionaries to `send_image/audio/video/file` is rejected; use
`summary=...` and `artifact_metadata=...`, or pass an `ArtifactRef`.

Non-text sends that write history should provide `history_text`:

```python
ctx.output.send_image(
    "chart.png",
    caption="Trend chart",
    history_text="Sent a trend chart.",
)
```

If a non-text delivery writes history without usable history text, later model
context has no text representation for that delivery.

## Status Sends

```python
ctx.output.progress("Working...")
ctx.output.notice("Skipped optional step")
```

| Method | Meaning |
| --- | --- |
| `progress(text, visible=True, delivery_metadata=None)` | Emit transient progress. |
| `notice(text, visible=True, delivery_metadata=None)` | Emit a transient notice. |

`progress` and `notice` always use transient history and do not accept
`history_policy`.

## Low-Level `send`

`ctx.input.send(...)` and `ctx.output.send(...)` accept one content block, a
string, a mapping compatible with `ContentBlock`, or a list of those values.
Most slot code should prefer the typed helpers above.

Valid content block types are `text`, `image`, `audio`, `video`, `file`, and
`control`. Valid delivery kinds are `message`, `progress`, and `notice`.

## History Policies

Valid delivery history policies are:

| Policy | Behavior |
| --- | --- |
| `persist` | Write assistant history and include it in later model context. |
| `model_hidden` | Write assistant history but hide it from later model context. |
| `transient` | Deliver output without writing assistant history. |

`write_history=True` maps to `persist`. `write_history=False` maps to
`transient` unless `history_policy` is explicitly supplied.

## Visibility

| Shape | Meaning |
| --- | --- |
| `visible=True, history_policy="persist"` | User-visible assistant history and model-visible context. |
| `visible=True, history_policy="model_hidden"` | User-visible history that later model context does not see. |
| `visible=True, history_policy="transient"` | Delivery only. |
| `visible=False, history_policy="persist"` | Hidden assistant history for later model context. |
| `visible=False, history_policy="model_hidden"` | Hidden durable history. |

`visible=False` with transient delivery has no user or history effect and is
rejected.

## Slot Defaults and Parallel Limits

`slot.yaml` can set:

```yaml
history_policy: persist
```

The delivery request can override that default with `history_policy=...`.

Serial output slots default to writing history. Parallel output slots and
background output paths cannot write session history. If a parallel output slot
requests a non-transient history policy, the runtime raises `RuntimeError`.

Input slots default their `ctx.input.send_*` history writes to transient, since
input slots run before the assistant response.

## Delivery Timing

The current authored SDK does not expose `live` or delayed-delivery parameters.
`send_*`, `progress`, and `notice` submit immediate host delivery requests. The
host owns session route lookup, artifact storage, dispatch status,
retry/degradation events, and final session records.

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

Child agent sessions do not inherit the parent's route. Their deliveries are
sent only to a route explicitly bound for the child session; otherwise they
become `unrouted`.

## Channel Fallback

Every delivery has text fallback where possible. Some channels may render only
text for a media delivery. The host records degraded delivery events when a
channel cannot render the richer block type.

## Boundary

Authored modules request delivery. The host owns persistence, artifacts,
session route lookup, channel dispatch, delivery status, and history
visibility.
