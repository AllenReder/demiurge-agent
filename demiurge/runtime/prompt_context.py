from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from demiurge.core import LoadedCore
from demiurge.providers import LLMMessage
from demiurge.runtime.context import ContextAssembler
from demiurge.runtime.interactions import (
    InteractionDelivery,
    InteractionItem,
    InteractionOutbound,
    SessionInteractionRouter,
)
from demiurge.runtime.session import SessionRuntime
from demiurge.sdk import ContextContribution, TurnContext


@dataclass(frozen=True, slots=True)
class PromptBuildRequest:
    core: LoadedCore
    context: list[ContextContribution]
    turn_messages: list[LLMMessage]
    turn_id: str
    step_id: str
    use_bootstrap_context: bool = True


@dataclass(frozen=True, slots=True)
class PromptDebugRequest:
    messages: list[LLMMessage]
    turn: TurnContext
    step_id: str
    interaction_metadata: dict[str, Any]


class PromptContextRuntime:
    """Builds provider prompt context and handles prompt-context debug delivery."""

    def __init__(
        self,
        *,
        assembler: ContextAssembler,
        sessions: SessionRuntime,
        interaction_router: SessionInteractionRouter,
        session_id: Callable[[], str],
        show_system_prompt: Callable[[], bool],
        emit_event: Callable[..., dict[str, Any]],
    ) -> None:
        self.assembler = assembler
        self.sessions = sessions
        self.interaction_router = interaction_router
        self.session_id = session_id
        self.show_system_prompt = show_system_prompt
        self.emit_event = emit_event

    def build_messages(self, request: PromptBuildRequest) -> list[LLMMessage]:
        current_session_id = self.session_id()
        assembled = self.assembler.assemble(
            core=request.core,
            context=request.context,
            session_history=[
                message
                for message in self.sessions.history_for_context(current_session_id)
                if message.turn_id != request.turn_id
            ],
            current_turn_messages=request.turn_messages,
            bootstrap_context=(
                self.sessions.read_bootstrap_context(current_session_id)
                if request.use_bootstrap_context
                else None
            ),
            compaction_summary=self.sessions.latest_compaction_summary(current_session_id),
        )
        self.emit_event(
            "context.assembled",
            turn_id=request.turn_id,
            step_id=request.step_id,
            layers=assembled.layer_summaries(),
            total_messages=len(assembled.messages),
            total_chars=sum(len(message.content or "") for message in assembled.messages),
        )
        return assembled.messages

    async def deliver_system_prompt_debug(self, request: PromptDebugRequest) -> None:
        if not self.show_system_prompt():
            return
        system_messages = [
            message
            for message in request.messages
            if message.role == "system" and (message.content or "").strip()
        ]
        if not system_messages:
            self.emit_event(
                "debug.system_prompt.skipped",
                turn_id=request.turn.turn_id,
                step_id=request.step_id,
                reason="no_system_messages",
                **request.interaction_metadata,
            )
            return

        channel = request.interaction_metadata.get("channel")
        if not channel:
            self.emit_event(
                "debug.system_prompt.skipped",
                turn_id=request.turn.turn_id,
                step_id=request.step_id,
                reason="no_channel",
                system_messages=len(system_messages),
                total_chars=sum(len(message.content or "") for message in system_messages),
                **request.interaction_metadata,
            )
            return

        text = self._format_system_prompt_debug(
            system_messages,
            turn_id=request.turn.turn_id,
            step_id=request.step_id,
        )
        metadata = {
            "role": "system",
            "debug": "system_prompt",
            "level": "info",
            "history_policy": "transient",
            "delivery": "immediate",
            "delivery_status": "pending",
            "system_messages": len(system_messages),
        }
        delivery = InteractionDelivery(
            type="text",
            kind="notice",
            text=text,
            fallback_text=text,
            blocks=[{"type": "text", "text": text, "metadata": {"debug": "system_prompt"}}],
            payload={"type": "text", "text": text},
            visible=True,
            history_policy="transient",
            metadata=metadata,
        )
        item = InteractionItem.delivery_item(delivery)
        outbound = InteractionOutbound(
            channel=str(channel),
            items=[item],
            session_id=self.session_id(),
            turn_id=request.turn.turn_id,
            metadata=dict(request.interaction_metadata),
        )
        try:
            result = await self.interaction_router.deliver(outbound)
            self.emit_event(
                "debug.system_prompt.unrouted" if result.status == "unrouted" else "debug.system_prompt.delivered",
                turn_id=request.turn.turn_id,
                step_id=request.step_id,
                system_messages=len(system_messages),
                total_chars=sum(len(message.content or "") for message in system_messages),
                **request.interaction_metadata,
            )
        except Exception as exc:
            item.set_dispatch_status("failed")
            self.emit_event(
                "debug.system_prompt.failed",
                turn_id=request.turn.turn_id,
                step_id=request.step_id,
                error=str(exc),
                system_messages=len(system_messages),
                total_chars=sum(len(message.content or "") for message in system_messages),
                **request.interaction_metadata,
            )

    def _format_system_prompt_debug(self, messages: list[LLMMessage], *, turn_id: str, step_id: str) -> str:
        sections = [
            "# System prompt debug",
            "",
            f"turn: {turn_id}",
            f"step: {step_id}",
        ]
        sections.extend(
            [
                "",
                "## Final system prompt",
                "",
                "\n\n".join(message.content or "" for message in messages),
            ]
        )
        return "\n".join(sections).strip()
