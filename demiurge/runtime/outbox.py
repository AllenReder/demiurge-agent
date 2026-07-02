from __future__ import annotations

from typing import Any, Mapping

from demiurge.runtime.interactions import InteractionBridge, InteractionItem, InteractionOutbound
from demiurge.runtime.store import RuntimeEvent, RuntimeQuery, RuntimeStore
from demiurge.storage import EventLog


class DeliveryRuntime:
    """Dispatches delivery intents and projects outbox status."""

    def __init__(self, *, store: RuntimeStore, event_log: EventLog):
        self.store = store
        self.event_log = event_log

    async def dispatch_item(
        self,
        item: InteractionItem,
        *,
        session_id: str,
        turn_id: str,
        channel: str,
        metadata: Mapping[str, Any],
        interaction_bridge: InteractionBridge,
        event_metadata: Mapping[str, Any] | None = None,
    ) -> None:
        outbound = InteractionOutbound(
            channel=channel,
            items=[item],
            session_id=session_id,
            turn_id=turn_id,
            metadata=dict(metadata),
        )
        try:
            await interaction_bridge.deliver(outbound)
            item.set_dispatch_status("delivered")
            delivery_id = self._delivery_id(item)
            if delivery_id:
                self._append(
                    RuntimeEvent(
                        type="delivery.sent",
                        aggregate_type="delivery",
                        aggregate_id=delivery_id,
                        payload={"status": "sent", "attempts": self.delivery_attempts(delivery_id) + 1},
                    )
                )
        except Exception as exc:
            self.mark_failed(
                item,
                turn_id=turn_id,
                error=str(exc),
                reason="bridge_deliver_failed",
                event_metadata=event_metadata,
            )

    def mark_failed(
        self,
        item: InteractionItem,
        *,
        turn_id: str,
        error: str | None,
        reason: str,
        event_metadata: Mapping[str, Any] | None = None,
    ) -> None:
        item.metadata["dispatch_error"] = error or reason
        if item.delivery is not None:
            failure_text = item.delivery.metadata.get("failure_history_text")
            message_id = item.delivery.metadata.get("message_id")
            if message_id and failure_text is not None:
                self._append(
                    RuntimeEvent(
                        type="message.updated",
                        aggregate_type="message",
                        aggregate_id=str(message_id),
                        payload={
                            "content": {
                                "text": str(failure_text),
                                "kind": "message",
                                "model_visible": item.delivery.history_policy == "persist",
                                "metadata": {
                                    **dict(item.delivery.metadata),
                                    "delivery_status": "failed",
                                },
                            }
                        },
                    )
                )
            item.delivery.metadata = {
                **dict(item.delivery.metadata),
                "delivery_error": error or reason,
            }
            delivery_id = self._delivery_id(item)
            if delivery_id:
                self._append(
                    RuntimeEvent(
                        type="delivery.failed",
                        aggregate_type="delivery",
                        aggregate_id=delivery_id,
                        payload={
                            "status": "failed",
                            "attempts": self.delivery_attempts(delivery_id) + 1,
                            "last_error": error or reason,
                        },
                    )
                )
        item.set_dispatch_status("failed")
        self.event_log.emit(
            "delivery.failed",
            turn_id=turn_id,
            reason=reason,
            **({"error": error} if error else {}),
            **dict(event_metadata or {}),
        )

    def delivery_attempts(self, delivery_id: str) -> int:
        rows = self.store.query(RuntimeQuery(table="outbox", where={"delivery_id": delivery_id}, limit=1)).rows
        if not rows:
            return 0
        return int(rows[0].get("attempts") or 0)

    def _delivery_id(self, item: InteractionItem) -> str | None:
        if item.delivery is None:
            return None
        delivery_id = item.delivery.metadata.get("delivery_id")
        return str(delivery_id) if delivery_id else None

    def _append(self, event: RuntimeEvent) -> None:
        self.store.append([event])
