from __future__ import annotations

import contextlib
from typing import Any, Mapping

from demiurge.runtime.durable_work import DurableClaim, DurableClaimConflict, DurableWorkRuntime, DurableWorkSpec
from demiurge.runtime.interactions import InteractionItem, InteractionOutbound, SessionInteractionRouter
from demiurge.runtime.store import RuntimeEvent, RuntimeQuery, RuntimeStore
from demiurge.storage import EventLog


class DeliveryRuntime:
    """Dispatches delivery intents and projects outbox status."""

    def __init__(self, *, store: RuntimeStore, event_log: EventLog, router: SessionInteractionRouter):
        self.store = store
        self.event_log = event_log
        self.router = router
        self.work = DurableWorkRuntime(store)

    async def dispatch_item(
        self,
        item: InteractionItem,
        *,
        session_id: str,
        turn_id: str,
        channel: str,
        metadata: Mapping[str, Any],
        event_metadata: Mapping[str, Any] | None = None,
    ) -> None:
        delivery_id = self._delivery_id(item)
        claim = self._claim_delivery(delivery_id)
        if delivery_id and claim is None:
            item.set_dispatch_status("unknown")
            return
        if claim is not None:
            self.work.mark_sending(claim)
            self._append(
                RuntimeEvent(
                    type="delivery.sending",
                    aggregate_type="delivery",
                    aggregate_id=claim.work_id,
                    payload={"status": "sending", "attempts": claim.attempt},
                )
            )
        outbound = InteractionOutbound(
            channel=channel,
            items=[item],
            session_id=session_id,
            turn_id=turn_id,
            metadata=dict(metadata),
        )
        try:
            result = await self.router.deliver(outbound)
            if result.status == "unrouted":
                self.mark_unrouted(
                    item,
                    turn_id=turn_id,
                    event_metadata=event_metadata,
                    attempts=claim.attempt if claim is not None else None,
                )
                if claim is not None:
                    self.work.succeed(claim)
                return
            item.set_dispatch_status("delivered")
            if delivery_id:
                if claim is not None:
                    self.work.succeed(claim)
                self._append(
                    RuntimeEvent(
                        type="delivery.sent",
                        aggregate_type="delivery",
                        aggregate_id=delivery_id,
                        payload={
                            "status": "sent",
                            "attempts": claim.attempt if claim is not None else self.delivery_attempts(delivery_id) + 1,
                        },
                    )
                )
        except Exception as exc:
            if claim is not None:
                with contextlib.suppress(DurableClaimConflict):
                    self.work.fail(claim, error=str(exc))
            self.mark_failed(
                item,
                turn_id=turn_id,
                error=str(exc),
                reason="bridge_deliver_failed",
                event_metadata=event_metadata,
                attempts=claim.attempt if claim is not None else None,
            )

    def mark_unrouted(
        self,
        item: InteractionItem,
        *,
        turn_id: str,
        event_metadata: Mapping[str, Any] | None = None,
        attempts: int | None = None,
    ) -> None:
        item.metadata["dispatch_error"] = "no_interactive_route"
        if item.delivery is not None:
            item.delivery.metadata = {
                **dict(item.delivery.metadata),
                "delivery_error": "no_interactive_route",
            }
            delivery_id = self._delivery_id(item)
            if delivery_id:
                self._append(
                    RuntimeEvent(
                        type="delivery.unrouted",
                        aggregate_type="delivery",
                        aggregate_id=delivery_id,
                        payload={
                            "status": "unrouted",
                            "attempts": attempts if attempts is not None else self.delivery_attempts(delivery_id) + 1,
                            "last_error": "no_interactive_route",
                        },
                    )
                )
        item.set_dispatch_status("unrouted")
        self.event_log.emit(
            "delivery.unrouted",
            turn_id=turn_id,
            reason="no_interactive_route",
            **dict(event_metadata or {}),
        )

    def mark_failed(
        self,
        item: InteractionItem,
        *,
        turn_id: str,
        error: str | None,
        reason: str,
        event_metadata: Mapping[str, Any] | None = None,
        attempts: int | None = None,
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
                            "attempts": attempts if attempts is not None else self.delivery_attempts(delivery_id) + 1,
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

    def recover(self) -> dict[str, int]:
        summary = self.work.recover()
        rows = self.store.query(RuntimeQuery(table="runtime_work_items", where={"kind": "delivery.send"}, limit=10_000)).rows
        for row in rows:
            if row.get("status") != "unknown":
                continue
            outbox = self.store.query(RuntimeQuery(table="outbox", where={"delivery_id": str(row["work_id"])}, limit=1)).rows
            if outbox and outbox[0].get("status") == "unknown":
                continue
            self._append(
                RuntimeEvent(
                    type="delivery.unknown",
                    aggregate_type="delivery",
                    aggregate_id=str(row["work_id"]),
                    payload={"status": "unknown", "last_error": row.get("last_error")},
                )
            )
        return summary

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

    def _claim_delivery(self, delivery_id: str | None) -> DurableClaim | None:
        if not delivery_id:
            return None
        rows = self.store.query(RuntimeQuery(table="runtime_work_items", where={"work_id": delivery_id}, limit=1)).rows
        if not rows:
            self.work.enqueue(DurableWorkSpec(work_id=delivery_id, kind="delivery.send"))
        return self.work.claim(delivery_id, owner_id="host.delivery_runtime")

    def _append(self, event: RuntimeEvent) -> None:
        self.store.append([event])
