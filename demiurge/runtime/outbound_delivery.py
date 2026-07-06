from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Literal, Protocol

from demiurge.runtime.interactions import (
    InteractionDelivery,
    InteractionOutbound,
    ToolInteractionRecord,
    UserPromptRequest,
)
from demiurge.tools.records import ToolExecutionRecord

OutboundDeliveryKind = Literal["delivery", "deliveries", "tool_call", "tool_calls", "tool_results", "prompt"]


class TextToolCallDelivery(Protocol):
    def __call__(self, record: ToolInteractionRecord, *, outbound: InteractionOutbound) -> Awaitable[None]:
        ...


class TextToolResultsDelivery(Protocol):
    def __call__(self, records: list[ToolExecutionRecord], *, outbound: InteractionOutbound) -> Awaitable[None]:
        ...


class TextDeliveryDelivery(Protocol):
    def __call__(self, delivery: InteractionDelivery, *, outbound: InteractionOutbound) -> Awaitable[None]:
        ...


class TextPromptDelivery(Protocol):
    def __call__(self, prompt: UserPromptRequest) -> Awaitable[str]:
        ...


@dataclass(frozen=True, slots=True)
class TextOutboundTarget:
    source: str
    reply_to: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class OutboundDeliveryStep:
    kind: OutboundDeliveryKind
    deliveries: tuple[InteractionDelivery, ...] = field(default_factory=tuple)
    tool_call: ToolInteractionRecord | None = None
    tool_calls: tuple[ToolInteractionRecord, ...] = field(default_factory=tuple)
    tool_results: tuple[ToolExecutionRecord, ...] = field(default_factory=tuple)
    prompt: UserPromptRequest | None = None


@dataclass(frozen=True, slots=True)
class TextOutboundDeliveryRuntime:
    deliver_tool_call: TextToolCallDelivery
    deliver_tool_results: TextToolResultsDelivery
    deliver_delivery: TextDeliveryDelivery
    prompt_user: TextPromptDelivery

    async def deliver(self, outbound: InteractionOutbound) -> None:
        try:
            for step in text_delivery_steps(outbound):
                if step.kind == "tool_call" and step.tool_call is not None:
                    await self.deliver_tool_call(step.tool_call, outbound=outbound)
                    continue
                if step.kind == "tool_results":
                    await self.deliver_tool_results(list(step.tool_results), outbound=outbound)
                    continue
                if step.kind == "delivery" and step.deliveries:
                    await self.deliver_delivery(step.deliveries[0], outbound=outbound)
                    continue
                if step.kind == "prompt" and step.prompt is not None:
                    await self.prompt_user(step.prompt)
        finally:
            outbound.mark_delivered()


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


def text_outbound_target(outbound: InteractionOutbound) -> TextOutboundTarget | None:
    source = outbound.metadata.get("source")
    if source is None:
        return None
    reply_to = outbound.metadata.get("reply_to")
    return TextOutboundTarget(
        source=str(source),
        reply_to=str(reply_to) if reply_to is not None else None,
        metadata=dict(outbound.metadata),
    )


def delivery_text_chunks(delivery: InteractionDelivery) -> list[str]:
    if not delivery.blocks:
        text = delivery.text or delivery.fallback_text
        return [text] if text else []
    chunks: list[str] = []
    for block in delivery.blocks:
        block_type = str(block.get("type") or "text")
        if block_type == "text":
            text = str(block.get("text") or "")
            if text:
                chunks.append(text)
            continue
        fallback = media_block_fallback(block)
        if fallback:
            chunks.append(fallback)
    if not chunks and (delivery.text or delivery.fallback_text):
        chunks.append(delivery.text or delivery.fallback_text)
    return chunks


def media_block_fallback(block: dict[str, Any]) -> str:
    artifact = block.get("artifact")
    if not isinstance(artifact, dict):
        return ""
    summary = artifact.get("summary") or artifact.get("media_type") or artifact.get("kind") or block.get("type")
    artifact_id = artifact.get("artifact_id") or "artifact"
    caption = block.get("text")
    prefix = f"{caption}\n" if caption else ""
    return f"{prefix}[artifact:{artifact_id} {artifact.get('kind') or block.get('type')} {summary}]"
