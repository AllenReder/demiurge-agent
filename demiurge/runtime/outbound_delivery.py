from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from demiurge.runtime.interactions import (
    InteractionDelivery,
    InteractionOutbound,
    ToolInteractionRecord,
    UserPromptRequest,
)
from demiurge.tools.records import ToolExecutionRecord

OutboundDeliveryKind = Literal["delivery", "deliveries", "tool_call", "tool_calls", "tool_results", "prompt"]


@dataclass(frozen=True, slots=True)
class OutboundDeliveryStep:
    kind: OutboundDeliveryKind
    deliveries: tuple[InteractionDelivery, ...] = field(default_factory=tuple)
    tool_call: ToolInteractionRecord | None = None
    tool_calls: tuple[ToolInteractionRecord, ...] = field(default_factory=tuple)
    tool_results: tuple[ToolExecutionRecord, ...] = field(default_factory=tuple)
    prompt: UserPromptRequest | None = None


def text_delivery_steps(outbound: InteractionOutbound) -> list[OutboundDeliveryStep]:
    steps: list[OutboundDeliveryStep] = []
    pending_tool_results: list[ToolExecutionRecord] = []

    def flush_tool_results() -> None:
        if pending_tool_results:
            steps.append(OutboundDeliveryStep(kind="tool_results", tool_results=tuple(pending_tool_results)))
            pending_tool_results.clear()

    for item in outbound.items:
        if item.kind == "tool_call" and item.tool_call is not None:
            flush_tool_results()
            steps.append(OutboundDeliveryStep(kind="tool_call", tool_call=item.tool_call))
            continue
        if item.kind == "tool_result" and item.tool_result is not None:
            pending_tool_results.append(item.tool_result)
            continue
        if item.kind == "delivery" and item.delivery is not None:
            flush_tool_results()
            steps.append(OutboundDeliveryStep(kind="delivery", deliveries=(item.delivery,)))

    flush_tool_results()
    _append_prompt_step(steps, outbound)
    return steps


def ui_delivery_steps(outbound: InteractionOutbound) -> list[OutboundDeliveryStep]:
    steps: list[OutboundDeliveryStep] = []
    pending_tool_calls: list[ToolInteractionRecord] = []
    pending_deliveries: list[InteractionDelivery] = []

    def flush_tool_calls() -> None:
        if pending_tool_calls:
            steps.append(OutboundDeliveryStep(kind="tool_calls", tool_calls=tuple(pending_tool_calls)))
            pending_tool_calls.clear()

    def flush_deliveries() -> None:
        if pending_deliveries:
            steps.append(OutboundDeliveryStep(kind="deliveries", deliveries=tuple(pending_deliveries)))
            pending_deliveries.clear()

    for item in outbound.items:
        if item.kind == "tool_call" and item.tool_call is not None:
            flush_deliveries()
            pending_tool_calls.append(item.tool_call)
            continue
        if item.kind == "tool_result" and item.tool_result is not None:
            flush_deliveries()
            pending_tool_calls.append(ToolInteractionRecord.finished(item.tool_result))
            continue
        if item.kind == "delivery" and item.delivery is not None:
            flush_tool_calls()
            pending_deliveries.append(item.delivery)

    flush_tool_calls()
    flush_deliveries()
    _append_prompt_step(steps, outbound)
    return steps


def _append_prompt_step(steps: list[OutboundDeliveryStep], outbound: InteractionOutbound) -> None:
    if outbound.prompt is not None:
        steps.append(OutboundDeliveryStep(kind="prompt", prompt=outbound.prompt))
