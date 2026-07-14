from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from demiurge.core import SlotDefinition
from demiurge.runtime.delivery import DeliveryRequest, DeliveryRouteContext
from demiurge.runtime.interaction_dispatch import InteractionDispatchRuntime
from demiurge.runtime.interactions import InteractionItem
from demiurge.runtime.module_delivery import ModuleDeliveryRuntime
from demiurge.runtime.slot_context import ModuleIOClient, ModuleResultClient
from demiurge.sdk import ContextContribution, TurnContext


class SlotEffectRuntime:
    """Commits authored slot IO through runtime-owned delivery and dispatch seams."""

    def __init__(
        self,
        *,
        home: Path,
        workspace: str | None,
        module_delivery: ModuleDeliveryRuntime,
        dispatch: InteractionDispatchRuntime,
        on_history_changed: Callable[[], None] | None = None,
    ) -> None:
        self.home = home
        self.workspace = workspace
        self.module_delivery = module_delivery
        self.dispatch = dispatch
        self.on_history_changed = on_history_changed

    def result_client(self, *, session_id: str, writable: bool) -> ModuleResultClient:
        return ModuleResultClient(
            home=self.home,
            session_id=session_id,
            workspace=self.workspace,
            writable=writable,
        )

    def module_io_client(
        self,
        slot: SlotDefinition,
        *,
        turn: TurnContext,
        interaction_metadata: dict[str, Any],
        background: bool = False,
        items: list[InteractionItem] | None = None,
    ) -> ModuleIOClient:
        default_write_history = slot.history_policy != "transient"
        allow_write_history = True
        if slot.kind == "input":
            default_write_history = False
        elif slot.kind == "output":
            default_write_history = not background
            allow_write_history = not background
        return ModuleIOClient(
            home=self.home,
            session_id=turn.session_id,
            workspace=self.workspace,
            default_history_policy=slot.history_policy,
            default_write_history=default_write_history,
            allow_write_history=allow_write_history,
            commit=lambda request: self.commit_delivery_request(
                request,
                turn=turn,
                slot=slot,
                interaction_metadata=interaction_metadata,
            ),
            schedule=lambda item: self.dispatch.schedule(
                item,
                turn=turn,
                interaction_metadata=interaction_metadata,
            ),
            route=self._delivery_route_context(turn, slot, interaction_metadata),
            background=background,
            items=items,
        )

    def commit_delivery_request(
        self,
        request: DeliveryRequest,
        *,
        turn: TurnContext,
        slot: SlotDefinition,
        interaction_metadata: dict[str, Any],
    ) -> InteractionItem | None:
        delivery = self.module_delivery.apply_request(
            request,
            turn=turn,
            slot=slot,
            interaction_metadata=interaction_metadata,
        )
        if self.on_history_changed is not None:
            self.on_history_changed()
        return InteractionItem.delivery_item(delivery) if delivery is not None else None

    def schedule_interaction_item(
        self,
        item: InteractionItem,
        *,
        turn: TurnContext,
        interaction_metadata: dict[str, Any],
    ) -> None:
        self.dispatch.schedule(item, turn=turn, interaction_metadata=interaction_metadata)

    async def dispatch_interaction_item_now(
        self,
        item: InteractionItem,
        *,
        turn: TurnContext,
        interaction_metadata: dict[str, Any],
    ) -> None:
        await self.dispatch.dispatch_now(item, turn=turn, interaction_metadata=interaction_metadata)

    async def flush_background_items(
        self,
        items: list[InteractionItem],
        *,
        turn: TurnContext,
        interaction_metadata: dict[str, Any],
    ) -> None:
        await self.dispatch.flush_pending(items, turn=turn, interaction_metadata=interaction_metadata)

    def schedule_slot_end_delivery_items(
        self,
        items: list[InteractionItem],
        *,
        turn: TurnContext,
        interaction_metadata: dict[str, Any],
    ) -> None:
        for item in items:
            self.schedule_interaction_item(item, turn=turn, interaction_metadata=interaction_metadata)

    def mark_pending_failed(self, items: list[InteractionItem], *, reason: str) -> None:
        self.dispatch.mark_pending_failed(items, reason=reason)

    def normalize_context_items(
        self,
        items: list[ContextContribution | dict[str, Any]],
        *,
        default_placement: str = "pre_current_user",
    ) -> list[ContextContribution]:
        result: list[ContextContribution] = []
        for item in items:
            if isinstance(item, ContextContribution):
                if not item.placement:
                    item.placement = default_placement
                result.append(item)
            elif isinstance(item, dict):
                data = dict(item)
                data.setdefault("placement", default_placement)
                result.append(ContextContribution(**data))
        return result

    def _delivery_route_context(
        self,
        turn: TurnContext,
        slot: SlotDefinition,
        interaction_metadata: dict[str, Any],
    ) -> DeliveryRouteContext:
        return DeliveryRouteContext(
            session_id=turn.session_id,
            turn_id=turn.turn_id,
            channel=interaction_metadata.get("channel"),
            conversation_key=interaction_metadata.get("conversation_key"),
            source=interaction_metadata.get("source"),
            reply_to=interaction_metadata.get("reply_to"),
            slot=slot.relative_path,
            metadata=dict(interaction_metadata),
        )
