from __future__ import annotations

import contextvars
import inspect
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from demiurge.security.approval import ApprovalDecision, ApprovalRequest
from demiurge.tools.records import ToolExecutionRecord


@dataclass(slots=True)
class InteractionInbound:
    channel: str
    text: str
    source: str
    reply_to: str | None = None
    conversation_key: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class InteractionDelivery:
    type: str = "text"
    kind: str = "message"
    text: str = ""
    blocks: list[dict[str, Any]] = field(default_factory=list)
    fallback_text: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    visible: bool = True
    history_policy: str = "persist"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class InteractionItem:
    kind: str
    delivery: InteractionDelivery | None = None
    tool_result: ToolExecutionRecord | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    dispatch_status: str = "pending"

    @classmethod
    def delivery_item(cls, delivery: InteractionDelivery) -> "InteractionItem":
        metadata = dict(delivery.metadata)
        status = str(metadata.get("dispatch_status") or metadata.get("delivery_status") or "pending")
        metadata["dispatch_status"] = status
        return cls(kind="delivery", delivery=delivery, metadata=metadata, dispatch_status=status)

    @classmethod
    def tool_result_item(cls, record: ToolExecutionRecord, *, metadata: dict[str, Any] | None = None) -> "InteractionItem":
        item_metadata = dict(metadata or {})
        status = str(item_metadata.get("dispatch_status") or "pending")
        item_metadata["dispatch_status"] = status
        return cls(kind="tool_result", tool_result=record, metadata=item_metadata, dispatch_status=status)

    def set_dispatch_status(self, status: str) -> None:
        self.dispatch_status = status
        self.metadata["dispatch_status"] = status
        if self.delivery is not None:
            delivery_metadata = dict(self.delivery.metadata)
            delivery_metadata["delivery_status"] = status
            self.delivery.metadata = delivery_metadata


@dataclass(slots=True)
class UserPromptRequest:
    question: str
    choices: list[str] = field(default_factory=list)
    session_id: str | None = None
    turn_id: str | None = None
    conversation_key: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, init=False)
class InteractionOutbound:
    channel: str
    items: list[InteractionItem]
    prompt: UserPromptRequest | None = None
    session_id: str | None = None
    turn_id: str | None = None
    metadata: dict[str, Any]
    on_delivered: Callable[[], None] | None = None

    def __init__(
        self,
        channel: str,
        *,
        items: list[InteractionItem] | None = None,
        prompt: UserPromptRequest | None = None,
        session_id: str | None = None,
        turn_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        on_delivered: Callable[[], None] | None = None,
    ) -> None:
        self.channel = channel
        self.items = list(items or [])
        self.prompt = prompt
        self.session_id = session_id
        self.turn_id = turn_id
        self.metadata = dict(metadata or {})
        self.on_delivered = on_delivered

    @property
    def deliveries(self) -> list[InteractionDelivery]:
        return [item.delivery for item in self.items if item.kind == "delivery" and item.delivery is not None]

    @property
    def tool_results(self) -> list[ToolExecutionRecord]:
        return [item.tool_result for item in self.items if item.kind == "tool_result" and item.tool_result is not None]

    def mark_delivered(self) -> None:
        for item in self.items:
            item.set_dispatch_status("delivered")
        callback = self.on_delivered
        self.on_delivered = None
        if callback is not None:
            callback()


class InteractionBridge(Protocol):
    async def deliver(self, outbound: InteractionOutbound) -> None:
        ...

    async def prompt_user(self, prompt: UserPromptRequest) -> str:
        ...

    async def request_approval(self, request: ApprovalRequest) -> ApprovalDecision:
        ...


_CURRENT_BRIDGE: contextvars.ContextVar[InteractionBridge | None] = contextvars.ContextVar(
    "demiurge_interaction_bridge",
    default=None,
)


def get_current_bridge() -> InteractionBridge | None:
    return _CURRENT_BRIDGE.get()


class BridgeApprovalProvider:
    name = "interaction_bridge"

    async def decide(self, request: ApprovalRequest) -> ApprovalDecision:
        bridge = _CURRENT_BRIDGE.get()
        if bridge is None:
            return ApprovalDecision("deny", "no active interaction bridge")
        decision = bridge.request_approval(request)
        if inspect.isawaitable(decision):
            decision = await decision
        return decision


class InteractionRuntime:
    def __init__(self, runner):
        self.runner = runner

    async def handle(
        self,
        inbound: InteractionInbound,
        *,
        bridge: InteractionBridge | None = None,
    ) -> InteractionOutbound:
        token = _CURRENT_BRIDGE.set(bridge)
        try:
            result = await self.runner.run_turn(inbound.text, interaction=inbound)
        finally:
            _CURRENT_BRIDGE.reset(token)
        prompt = self._prompt_from_tool_results(result, inbound)
        pending_items = [item for item in result.items if item.dispatch_status == "pending"]
        return InteractionOutbound(
            channel=inbound.channel,
            items=pending_items,
            prompt=prompt,
            session_id=result.session_id,
            turn_id=result.turn_id,
            metadata={
                "source": inbound.source,
                "reply_to": inbound.reply_to,
                "conversation_key": inbound.conversation_key,
                **dict(inbound.metadata or {}),
            },
        )

    def _prompt_from_tool_results(self, result, inbound: InteractionInbound) -> UserPromptRequest | None:
        if not result.needs_user:
            return None
        for record in reversed(result.tool_results):
            data = record.result.data
            if not isinstance(data, dict) or not data.get("needs_user"):
                continue
            question = str(data.get("question") or record.result.content or "").strip()
            choices = data.get("choices")
            return UserPromptRequest(
                question=question,
                choices=[str(choice) for choice in choices] if isinstance(choices, list) else [],
                session_id=result.session_id,
                turn_id=result.turn_id,
                conversation_key=inbound.conversation_key,
                metadata={
                    "channel": inbound.channel,
                    "source": inbound.source,
                    "reply_to": inbound.reply_to,
                    **dict(inbound.metadata or {}),
                },
            )
        return None
