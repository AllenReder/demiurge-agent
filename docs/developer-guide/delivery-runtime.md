# Delivery Runtime

Authored modules and tools describe delivery intent. The host turns that intent
into artifacts, session messages, events, and channel items.

## Core Types

Delivery data is represented by:

- `DeliveryRequest`
- `ContentBlock`
- `ArtifactInput`
- `ArtifactRef`
- `DeliveryRouteContext`
- `InteractionDelivery`

## Flow

```text
ctx.output.send_* / ctx.input.send_*
  -> DeliveryRequest
  -> history policy applied
  -> artifacts registered if needed
  -> session message/event written
  -> InteractionItem returned
  -> channel bridge renders item
```

## History Policy

The host decides whether a delivery is written to `messages.jsonl` and whether
it is model-visible later:

- `persist`
- `model_hidden`
- `transient`

See [../reference/history-policy-and-delivery.md](../reference/history-policy-and-delivery.md).

## Artifacts

Media/file deliveries accept workspace paths, session paths, URLs, or
host-returned `ArtifactRef` values. The host registers paths as artifacts before
delivery.

## Route Context

The inbound interaction supplies channel, conversation key, source, reply target
and metadata. Async delivery uses this route context so output can return to the
right channel conversation.

## Boundary

Delivery requests are not platform sends. Channel bridges decide final
rendering for TUI or Telegram.
