from __future__ import annotations

from typing import Any, Callable

from demiurge.core import SlotDefinition
from demiurge.runtime.interactions import InteractionItem
from demiurge.runtime.slot_context import SlotContextRuntime
from demiurge.runtime.slot_effects import SlotEffectRuntime
from demiurge.runtime.slots import InputSlotRunRequest, OutputSlotRunRequest, SlotInvocation, SlotRuntime
from demiurge.sdk import ContextContribution
from demiurge.security.capabilities import CapabilityFacade


class SlotExecutionRuntime:
    """Runs one authored input/output slot behind the Agent Slot execution contract."""

    def __init__(
        self,
        *,
        slot_runtime: SlotRuntime,
        slot_context: SlotContextRuntime,
        slot_effects: SlotEffectRuntime,
        emit_event: Callable[..., dict[str, Any]],
        refresh_history: Callable[[], None],
    ) -> None:
        self.slot_runtime = slot_runtime
        self.slot_context = slot_context
        self.slot_effects = slot_effects
        self.emit_event = emit_event
        self.refresh_history = refresh_history

    async def run_input(self, request: InputSlotRunRequest) -> list[InteractionItem]:
        slot = request.slot
        turn = request.turn
        items: list[InteractionItem] = []
        context_build = self.slot_context.build_input_context(request, items=items)
        ctx = context_build.context
        io_client = context_build.io_client
        try:
            prior_envelope_activations = set(request.envelope.activated_skills)
            value = await self._call_slot(slot, ctx)
            if value is not None:
                self.emit_event("module.return_ignored", turn_id=turn.turn_id, slot=slot.relative_path, kind="input")
            for name in [name for name in request.envelope.activated_skills if name not in prior_envelope_activations]:
                if name in request.activated:
                    continue
                self._require_skill_activation(request.capability, slot.relative_path, name)
                skill = request.core.skill_by_id(name)
                if skill is None:
                    request.activated.add(name)
                    self.emit_event(
                        "skill.activation_ignored",
                        turn_id=turn.turn_id,
                        slot=slot.relative_path,
                        skill=name,
                    )
                    continue
                request.activated.add(name)
                request.contributions.append(
                    ContextContribution(type="skill", key=skill.name, content=skill.content, placement="system_context")
                )
                self.emit_event("skill.activated", turn_id=turn.turn_id, slot=slot.relative_path, skill=skill.name)
            self.slot_effects.schedule_slot_end_delivery_items(
                io_client.slot_end_items,
                turn=turn,
                interaction_metadata=request.interaction_metadata,
            )
            self.emit_event("module.completed", turn_id=turn.turn_id, slot=slot.relative_path, kind="input")
            return io_client.items
        except Exception as exc:
            self.slot_effects.mark_pending_failed(io_client.slot_end_items, reason="slot_failed")
            self.emit_event(
                "module.failed",
                turn_id=turn.turn_id,
                slot=slot.relative_path,
                kind="input",
                error=str(exc),
            )
            if slot.failure_policy == "hard":
                raise
            return io_client.items

    async def run_output(self, request: OutputSlotRunRequest) -> list[InteractionItem]:
        slot = request.slot
        turn = request.turn
        items: list[InteractionItem] = []
        context_build = self.slot_context.build_output_context(request, items=items)
        ctx = context_build.context
        io_client = context_build.io_client
        try:
            value = await self._call_slot(slot, ctx)
            if value is not None:
                self.emit_event("module.return_ignored", turn_id=turn.turn_id, slot=slot.relative_path, kind="output")
            self.slot_effects.schedule_slot_end_delivery_items(
                io_client.slot_end_items,
                turn=turn,
                interaction_metadata=request.interaction_metadata,
            )
            self.refresh_history()
            self.emit_event(
                "module.completed",
                turn_id=turn.turn_id,
                slot=slot.relative_path,
                kind="output",
            )
            return io_client.items
        except Exception as exc:
            self.slot_effects.mark_pending_failed(io_client.slot_end_items, reason="slot_failed")
            self.emit_event(
                "module.failed",
                turn_id=turn.turn_id,
                slot=slot.relative_path,
                kind="output",
                error=str(exc),
            )
            if slot.failure_policy == "hard":
                raise
            return io_client.items

    async def _call_slot(self, slot: SlotDefinition, ctx: Any) -> Any:
        outcome = await self.slot_runtime.invoke(SlotInvocation(slot=slot, context=ctx, phase=slot.kind))
        outcome.raise_for_error()
        return outcome.value

    def _require_skill_activation(self, capability: CapabilityFacade, slot_path: str, name: str) -> None:
        scoped = f"skill.activate:{name}"
        if capability.can(scoped, slot_path=slot_path):
            capability.require(scoped, slot_path=slot_path)
            return
        capability.require("skill.activate", slot_path=slot_path)
