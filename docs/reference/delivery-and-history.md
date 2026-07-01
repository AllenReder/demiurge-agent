---
title: Delivery and History
description: Reference for output delivery timing and history policy.
---

# Delivery and History

Output modules can deliver text, artifacts, media, and structured results
through host-owned delivery interfaces.

## History Policy

Common policies:

| Policy | Meaning |
| --- | --- |
| `persist` | Store delivered content in durable session history. |
| `transient` | Deliver live output without durable assistant history. |

Use persisted delivery for assistant answers that should be available in later
context. Use transient delivery for progress, notices, and live-only status.

## Timing

Input and output modules can emit live deliveries before or after model output is
persisted. Do not assume live display order is identical to persisted history
order.

## Output Module Example

```python
def process(ctx):
    ctx.output.send_text(ctx.output.content, history_policy="persist")
```

## Boundary

Authored modules request delivery. The host owns session records, channel
delivery, route context, artifact records, and persistence.
