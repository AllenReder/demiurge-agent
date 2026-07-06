from __future__ import annotations

from pathlib import Path
from typing import Any, Awaitable, Callable

from demiurge.core import SlotDefinition
from demiurge.runtime.delivery import DeliveryRequest, DeliveryRouteContext
from demiurge.runtime.interaction_dispatch import InteractionDispatchRuntime
from demiurge.runtime.interactions import InteractionDelivery, InteractionItem
from demiurge.runtime.module_delivery import ModuleDeliveryRuntime
from demiurge.runtime.slot_context import ModuleIOClient, ModuleResultClient
from demiurge.providers import ToolCall
from demiurge.sdk import ContextContribution, DeliverEffect, EffectRequest, ToolResult, TurnContext
from demiurge.security.capabilities import CapabilityDenied, CapabilityFacade


ToolEffectExecutor = Callable[[ToolCall, Any, TurnContext, CapabilityFacade], Awaitable[ToolResult]]


class SlotEffectRuntime:
    """Commits authored slot effects through runtime-owned delivery and dispatch seams."""

    def __init__(
        self,
        *,
        home: Path,
        session_id: Callable[[], str],
        workspace: str | None,
        module_delivery: ModuleDeliveryRuntime,
        dispatch: InteractionDispatchRuntime,
        on_history_changed: Callable[[], None] | None = None,
        execute_tool_effect: ToolEffectExecutor | None = None,
        emit_event: Callable[..., dict[str, Any]] | None = None,
    ) -> None:
        self.home = home
        self._session_id = session_id
        self.workspace = workspace
        self.module_delivery = module_delivery
        self.dispatch = dispatch
        self.on_history_changed = on_history_changed
        self.execute_tool_effect = execute_tool_effect
        self.emit_event = emit_event or self._noop_event

    def result_client(self, *, writable: bool) -> ModuleResultClient:
        return ModuleResultClient(
            home=self.home,
            session_id=self._session_id(),
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
            session_id=self._session_id(),
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

    def apply_deliver_effect(
        self,
        effect: EffectRequest,
        *,
        turn: TurnContext,
        slot: SlotDefinition,
        interaction_metadata: dict[str, Any],
    ) -> InteractionDelivery | None:
        request = self.module_delivery.request_from_deliver_effect(effect, slot=slot)
        item = self.commit_delivery_request(
            request,
            turn=turn,
            slot=slot,
            interaction_metadata=interaction_metadata,
        )
        return item.delivery if item is not None else None

    async def handle_effects(
        self,
        effects: list[EffectRequest | DeliverEffect | dict[str, Any]],
        *,
        core: Any,
        turn: TurnContext,
        capability: CapabilityFacade,
        slot: SlotDefinition,
        interaction_metadata: dict[str, Any],
    ) -> list[InteractionDelivery]:
        deliveries: list[InteractionDelivery] = []
        for raw_effect in effects:
            effect = self.normalize_effect(raw_effect)
            try:
                if effect.type == "append_assistant_message" and effect.content:
                    effect = EffectRequest(
                        type="deliver",
                        payload={"type": "text", "text": effect.content},
                        visible=effect.visible,
                        history_policy=effect.history_policy,
                    )
                if effect.type == "deliver":
                    delivery = self.apply_deliver_effect(
                        effect,
                        turn=turn,
                        slot=slot,
                        interaction_metadata=interaction_metadata,
                    )
                    if delivery:
                        deliveries.append(delivery)
                elif effect.type == "append_assistant_message" and effect.content:
                    if effect.visible:
                        deliveries.append(
                            InteractionDelivery(
                                type="text",
                                text=effect.content,
                                payload={"type": "text", "text": effect.content},
                                visible=True,
                                history_policy=effect.history_policy or slot.history_policy,
                                metadata={"slot": slot.relative_path},
                            )
                        )
                elif effect.type == "evolve_core":
                    capability.require("tool.call:evolve_core", slot_path=slot.relative_path)
                    result = await self._execute_tool_effect(
                        ToolCall(name="evolve_core", arguments={"goal": effect.goal or effect.reason or ""}),
                        core=core,
                        turn=turn,
                        capability=capability,
                    )
                    deliveries.append(self._tool_effect_delivery(result, slot=slot, effect_type=effect.type))
                elif effect.type == "tool_call" and effect.tool_name:
                    capability.require(f"tool.call:{effect.tool_name}", slot_path=slot.relative_path)
                    result = await self._execute_tool_effect(
                        ToolCall(name=effect.tool_name, arguments=dict(effect.arguments or {})),
                        core=core,
                        turn=turn,
                        capability=capability,
                    )
                    if result.content:
                        deliveries.append(self._tool_effect_delivery(result, slot=slot, effect_type=effect.type))
                else:
                    self.emit_event(
                        "effect.ignored",
                        turn_id=turn.turn_id,
                        slot=slot.relative_path,
                        effect_type=effect.type,
                    )
            except CapabilityDenied as exc:
                self.emit_event(
                    "capability.denied",
                    turn_id=turn.turn_id,
                    slot=slot.relative_path,
                    error=str(exc),
                )
        return deliveries

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

    def normalize_effect(self, value: EffectRequest | DeliverEffect | dict[str, Any]) -> EffectRequest:
        if isinstance(value, DeliverEffect):
            return EffectRequest(
                type="deliver",
                payload=value.payload,
                attachments=list(value.attachments),
                visible=value.visible,
                history_policy=value.history_policy,
                target=value.target,
            )
        if isinstance(value, EffectRequest):
            return value
        return EffectRequest(**value)

    def _delivery_route_context(
        self,
        turn: TurnContext,
        slot: SlotDefinition,
        interaction_metadata: dict[str, Any],
    ) -> DeliveryRouteContext:
        return DeliveryRouteContext(
            session_id=self._session_id(),
            turn_id=turn.turn_id,
            channel=interaction_metadata.get("channel"),
            conversation_key=interaction_metadata.get("conversation_key"),
            source=interaction_metadata.get("source"),
            reply_to=interaction_metadata.get("reply_to"),
            slot=slot.relative_path,
            metadata=dict(interaction_metadata),
        )

    async def _execute_tool_effect(
        self,
        call: ToolCall,
        *,
        core: Any,
        turn: TurnContext,
        capability: CapabilityFacade,
    ) -> ToolResult:
        if self.execute_tool_effect is None:
            raise RuntimeError("slot effect tool execution is not configured")
        return await self.execute_tool_effect(call, core, turn, capability)

    @staticmethod
    def _tool_effect_delivery(result: ToolResult, *, slot: SlotDefinition, effect_type: str) -> InteractionDelivery:
        return InteractionDelivery(
            type="text",
            text=result.content,
            payload={"type": "text", "text": result.content},
            metadata={"slot": slot.relative_path, "effect": effect_type},
        )

    @staticmethod
    def _noop_event(event_type: str, **payload: Any) -> dict[str, Any]:
        return {"type": event_type, **payload}
