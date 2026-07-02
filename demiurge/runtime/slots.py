from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Literal

from demiurge.core import SlotDefinition, load_slot_callable
from demiurge.sdk import BootstrapContext, InputContext, OutputContext, ToolContext


SlotPhase = Literal["bootstrap", "input", "output", "tool"]
BootstrapSlotContext = BootstrapContext
InputSlotContext = InputContext
OutputSlotContext = OutputContext


@dataclass(frozen=True, slots=True)
class SlotInvocation:
    slot: SlotDefinition
    context: BootstrapSlotContext | InputSlotContext | OutputSlotContext | ToolContext | Any
    phase: SlotPhase | None = None
    background: bool = False


@dataclass(slots=True)
class SlotOutcome:
    slot_id: str
    phase: str
    status: Literal["completed", "failed"]
    value: Any = None
    error: str | None = None
    exception: BaseException | None = None
    background: bool = False

    @property
    def failed(self) -> bool:
        return self.status == "failed"

    def raise_for_error(self) -> None:
        if self.exception is not None:
            raise self.exception


class SlotRuntime:
    """Executes authored slot handlers behind a small runtime interface."""

    async def invoke(self, invocation: SlotInvocation) -> SlotOutcome:
        slot = invocation.slot
        phase = invocation.phase or slot.kind
        try:
            func = load_slot_callable(slot)
            value = func(invocation.context)
            if inspect.isawaitable(value):
                value = await value
            return SlotOutcome(
                slot_id=slot.slot_id,
                phase=phase,
                status="completed",
                value=value,
                background=invocation.background,
            )
        except Exception as exc:
            return SlotOutcome(
                slot_id=slot.slot_id,
                phase=phase,
                status="failed",
                error=str(exc),
                exception=exc,
                background=invocation.background,
            )
