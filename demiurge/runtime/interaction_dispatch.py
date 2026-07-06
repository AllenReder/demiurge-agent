from __future__ import annotations

import asyncio
from typing import Any, Callable, Mapping

from demiurge.runtime.interactions import InteractionDelivery, InteractionItem
from demiurge.sdk import TurnContext


class InteractionDispatchRuntime:
    """Owns interaction item dispatch status, routing metadata, and delivery tasks."""

    def __init__(
        self,
        *,
        session_id: Callable[[], str],
        delivery_runtime: Any,
        track_background_task: Callable[[asyncio.Task[Any]], None],
    ) -> None:
        self._session_id = session_id
        self.delivery_runtime = delivery_runtime
        self.track_background_task = track_background_task

    def schedule(
        self,
        item: InteractionItem,
        *,
        turn: TurnContext,
        interaction_metadata: dict[str, Any],
    ) -> None:
        prepared = self._prepare(item, interaction_metadata=interaction_metadata)
        if prepared is None:
            return
        channel, metadata = prepared
        task = asyncio.create_task(
            self.delivery_runtime.dispatch_item(
                item,
                session_id=self._session_id(),
                turn_id=turn.turn_id,
                channel=channel,
                metadata=metadata,
                event_metadata=self._delivery_event_metadata(metadata),
            )
        )
        self.track_background_task(task)

    async def dispatch_now(
        self,
        item: InteractionItem,
        *,
        turn: TurnContext,
        interaction_metadata: dict[str, Any],
    ) -> None:
        prepared = self._prepare(item, interaction_metadata=interaction_metadata)
        if prepared is None:
            return
        channel, metadata = prepared
        await self.delivery_runtime.dispatch_item(
            item,
            session_id=self._session_id(),
            turn_id=turn.turn_id,
            channel=channel,
            metadata=metadata,
            event_metadata=self._delivery_event_metadata(metadata),
        )

    async def flush_pending(
        self,
        items: list[InteractionItem],
        *,
        turn: TurnContext,
        interaction_metadata: dict[str, Any],
    ) -> None:
        for item in items:
            await self.dispatch_now(item, turn=turn, interaction_metadata=interaction_metadata)

    def mark_pending_failed(self, items: list[InteractionItem], *, reason: str) -> None:
        for item in items:
            if item.delivery is None or item.dispatch_status != "pending":
                continue
            item.metadata["delivery_failed_reason"] = reason
            item.delivery.metadata = {
                **dict(item.delivery.metadata),
                "delivery_failed_reason": reason,
            }
            item.set_dispatch_status("failed")

    def _prepare(
        self,
        item: InteractionItem,
        *,
        interaction_metadata: dict[str, Any],
    ) -> tuple[str, dict[str, Any]] | None:
        if item.dispatch_status != "pending":
            return None
        metadata = self._interaction_item_outbound_metadata(interaction_metadata, item)
        channel = metadata.get("channel") or interaction_metadata.get("channel")
        if not channel:
            item.set_dispatch_status("unrouted")
            return None
        item.set_dispatch_status("scheduled")
        return str(channel), metadata

    def _interaction_item_outbound_metadata(
        self,
        interaction_metadata: dict[str, Any],
        item: InteractionItem,
    ) -> dict[str, Any]:
        if item.delivery is not None:
            return self._background_outbound_metadata(interaction_metadata, [item.delivery])
        metadata = dict(interaction_metadata)
        for key in ("phase", "step_id", "tool_name", "tool_call_id", "is_error", "dispatch_status"):
            if item.metadata.get(key) is not None:
                metadata[key] = item.metadata[key]
        return metadata

    def _background_outbound_metadata(
        self,
        interaction_metadata: dict[str, Any],
        deliveries: list[InteractionDelivery],
    ) -> dict[str, Any]:
        metadata = dict(interaction_metadata)
        if not deliveries:
            return metadata
        delivery_metadata = deliveries[0].metadata
        route = delivery_metadata.get("route")
        if isinstance(route, Mapping):
            self._apply_route_metadata(metadata, route)
        for key in ("slot", "phase", "delivery_id", "kind", "history_policy", "delivery", "delivery_status", "background"):
            if delivery_metadata.get(key) is not None:
                metadata[key] = delivery_metadata[key]
        return metadata

    def _apply_route_metadata(self, metadata: dict[str, Any], route: Mapping[str, Any]) -> None:
        for key in ("session_id", "turn_id", "channel", "conversation_key", "source", "reply_to"):
            if route.get(key) is not None:
                metadata[key] = route[key]

    def _delivery_event_metadata(self, metadata: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in metadata.items() if key != "turn_id"}
