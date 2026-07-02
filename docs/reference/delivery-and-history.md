---
title: Delivery and History
description: Reference for output delivery and session history writes.
---

# Delivery and History

Output modules can deliver text, artifacts, media, and structured results
through host-owned delivery interfaces.

## History Writes

| Call shape | Meaning |
| --- | --- |
| `write_history=True` | Store delivered content in session history. |
| `write_history=False` | Deliver live output without durable assistant history. |
| `visible=False, write_history=True` | Store context for later turns without delivering to the user. |

Use persisted delivery for assistant answers that should be available in later
context. Use transient delivery for progress, notices, and live-only status.

Serial output slots default `write_history=True`. Parallel output slots default
`write_history=False` and cannot set it to `True`.

## Timing

Author-facing delivery timing parameters are removed. `send_*` methods submit a
delivery intent immediately; the host owns channel routing, persistence, retry,
and final delivery state.

```python
def process(ctx):
    ctx.output.send_text(ctx.output.response_text)
```

## Boundary

Authored modules request delivery. The host owns session records, channel
delivery, route context, artifact records, and persistence.
