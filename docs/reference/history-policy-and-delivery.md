# History Policy and Delivery

Authored code emits delivery requests. The host decides persistence, artifact
registration, route ordering, and channel rendering.

## History Policy

| Policy | Written to `messages.jsonl` | Enters later model context | Typical use |
| --- | --- | --- | --- |
| `persist` | Yes | Yes | Normal assistant replies. |
| `model_hidden` | Yes | No | User-visible records that should not affect later prompts. |
| `transient` | No | No | Progress, notices, and temporary status. |

When delivery calls omit `history_policy`, the host uses the current slot's
`history_policy`.

## Delivery Timing

| Delivery | Behavior |
| --- | --- |
| `immediate` | Commit history at the call site and queue channel delivery immediately. |
| `slot_end` | Commit history at the call site and queue delivery after the slot succeeds. |

`progress()` and `notice()` always use transient immediate delivery.

## Content Blocks

Delivery requests support text, image, audio, video, file, and control blocks.
Media/file `send_*` calls register artifacts and emit channel-appropriate
delivery.

## Examples

```python
ctx.output.send_text("Final answer", history_policy="persist")
ctx.output.send_text("Background result", history_policy="model_hidden")
ctx.output.progress("Working")
ctx.output.send_file("report.md", summary="Generated report")
```

## Success Check

Inspect:

```bash
tail -n 50 ~/.demiurge/sessions/<session_id>/messages.jsonl
tail -n 50 ~/.demiurge/sessions/<session_id>/events.jsonl
```

`transient` deliveries should appear in events but not messages.

## Boundary

Delivery policy does not grant permissions. Artifact paths must still stay
within allowed workspace/session boundaries or be valid URLs/host refs.
