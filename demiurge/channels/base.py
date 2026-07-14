from __future__ import annotations

import asyncio
import contextlib
import contextvars
import logging
from typing import Any, Callable, Protocol

from demiurge.channels.commands import ChannelCommandExecutor, ChannelCommandRuntime
from demiurge.runtime.conversation_lifecycle import ConversationLifecycleConfig, ConversationLifecycleRuntime
from demiurge.runtime.conversation_keys import build_conversation_key
from demiurge.runtime.interactions import (
    InteractionDelivery,
    InteractionInbound,
    InteractionOutbound,
    InteractionRuntime,
    SessionRouteBinding,
    ToolInteractionRecord,
    UserPromptRequest,
)
from demiurge.runtime.ingress import BusyInboundDecision, ConversationIngressState
from demiurge.runtime.outbound_delivery import TextOutboundDeliveryRuntime, delivery_text_chunks, text_outbound_target
from demiurge.runtime.prompts import PromptDeliveryRuntime
from demiurge.runtime.tool_display import normalize_tool_display, tool_call_markdown, tool_results_markdown
from demiurge.security.approval import ApprovalDecision, ApprovalRequest
from demiurge.security.redaction import (
    RedactionView,
    SecretValue,
    redact_exception,
)
from demiurge.slash import command_names_for_surface


logger = logging.getLogger(__name__)


class GatewayBridge(Protocol):
    async def run_forever(self) -> None:
        ...

    async def deliver(self, outbound: InteractionOutbound) -> None:
        ...

    async def prompt_user(self, prompt: UserPromptRequest) -> str:
        ...

    async def request_approval(self, request: ApprovalRequest) -> ApprovalDecision:
        ...


TextConversationState = ConversationIngressState

TEXT_CHANNEL_COMMAND_NAMES = command_names_for_surface("text")


class TextChannelBridgeBase:
    def __init__(
        self,
        *,
        channel_name: str,
        runtime: InteractionRuntime | None = None,
        runtime_factory: Callable[[str], InteractionRuntime] | None = None,
        busy_mode: str = "interrupt",
        tool_display: str = "summary",
    ) -> None:
        if runtime is None and runtime_factory is None:
            raise ValueError(f"{type(self).__name__} requires runtime or runtime_factory")
        self.channel_name = channel_name
        self._runtime_factory = runtime_factory or (lambda _conversation_key: runtime)  # type: ignore[return-value]
        self.default_busy_mode = busy_mode if busy_mode in {"interrupt", "queue"} else "interrupt"
        self.tool_display = normalize_tool_display(tool_display)
        self._command_runtime = ChannelCommandRuntime(
            command_names=TEXT_CHANNEL_COMMAND_NAMES,
            unavailable_template="Command not available: /{name}",
            unknown_template="Unknown command: /{name}",
        )
        self._prompt_delivery = PromptDeliveryRuntime()
        self._conversation_lifecycle = ConversationLifecycleRuntime(
            config=ConversationLifecycleConfig(
                channel=channel_name,
                merge_owner_id=f"bridge:{channel_name}:merge",
                enqueue_owner_id=f"bridge:{channel_name}:enqueue",
                require_source=True,
            ),
            state_factory=self._new_conversation_state,
            run_turn=self._run_inbound,
            notify_busy=self._notify_busy_inbound,
        )
        self._command_executor = ChannelCommandExecutor(
            channel_name=channel_name,
            surface="text",
            send_text=self._send_command_text,
            lifecycle=self._conversation_lifecycle,
            cancel_active=self._cancel_active,
            help_extra_lines=("- `/ask <prompt>` - send a prompt",),
        )
        self._conversations = self._conversation_lifecycle.states
        self._active_inbound: contextvars.ContextVar[InteractionInbound | None] = contextvars.ContextVar(
            f"demiurge_{channel_name}_active_inbound",
            default=None,
        )

    async def run_forever(self) -> None:
        raise NotImplementedError

    async def handle_inbound(self, inbound: InteractionInbound) -> None:
        if not inbound.principal_key:
            inbound.principal_key = inbound.conversation_key or build_conversation_key(
                self.channel_name,
                "source",
                inbound.source,
            )
        state = self._conversation_state(
            inbound.conversation_key or build_conversation_key(self.channel_name, "source", inbound.source)
        )
        self._conversation_lifecycle.remember_route(state, inbound)
        command_outcome = await self._handle_command(inbound, state)
        if command_outcome.handled:
            return
        inbound = command_outcome.inbound

        inbound = self._prompt_delivery.resolve_inbound(inbound)
        await self._conversation_lifecycle.submit_inbound(
            state,
            inbound,
            fallback_source=inbound.source,
        )

    async def deliver(self, outbound: InteractionOutbound) -> None:
        await self._text_outbound_delivery_runtime().deliver(outbound)

    def _text_outbound_delivery_runtime(self) -> TextOutboundDeliveryRuntime:
        return TextOutboundDeliveryRuntime(
            deliver_tool_call=self._deliver_tool_call,
            deliver_tool_results=self._deliver_tool_results,
            deliver_delivery=self._deliver_delivery,
            prompt_user=self.prompt_user,
        )

    async def prompt_user(self, prompt: UserPromptRequest) -> str:
        delivery = self._prompt_delivery.prepare(prompt)
        if delivery is None:
            return ""
        await self._send_text(
            delivery.source,
            delivery.text,
            reply_to=delivery.reply_to,
            metadata=delivery.metadata,
        )
        return ""

    async def request_approval(self, request: ApprovalRequest) -> ApprovalDecision:
        inbound = self._active_inbound.get()
        if inbound is not None:
            await self._send_text(
                inbound.source,
                "Approval prompts are not supported on this external channel; request denied.",
                reply_to=inbound.reply_to,
                metadata=inbound.metadata,
            )
        return ApprovalDecision("deny", f"{self.channel_name} approval prompts are not supported")

    async def _deliver_delivery(self, delivery: InteractionDelivery, *, outbound: InteractionOutbound) -> None:
        if not delivery.visible:
            return
        target = text_outbound_target(outbound)
        if target is None:
            return
        chunks = delivery_text_chunks(delivery)
        for index, chunk in enumerate(chunks):
            await self._send_text(
                target.source,
                chunk,
                reply_to=target.reply_to if target.reply_to is not None and index == 0 else None,
                metadata=target.metadata,
            )

    async def _deliver_tool_results(self, records: list[Any], *, outbound: InteractionOutbound) -> None:
        if self.tool_display == "quiet" or not records:
            return
        target = text_outbound_target(outbound)
        if target is None:
            return
        text = self._tool_results_text(records)
        if text:
            await self._send_text(
                target.source,
                text,
                reply_to=target.reply_to,
                metadata=target.metadata,
            )

    async def _deliver_tool_call(self, record: ToolInteractionRecord, *, outbound: InteractionOutbound) -> None:
        if self.tool_display == "quiet":
            return
        target = text_outbound_target(outbound)
        if target is None:
            return
        text = self._tool_call_text(record)
        if text:
            await self._send_text(
                target.source,
                text,
                reply_to=target.reply_to,
                metadata=target.metadata,
            )

    def _tool_call_text(self, record: ToolInteractionRecord) -> str:
        return tool_call_markdown(record, mode=self.tool_display)

    def _tool_results_text(self, records: list[Any]) -> str:
        return tool_results_markdown(records, mode=self.tool_display)

    def _conversation_state(self, conversation_key: str) -> TextConversationState:
        return self._conversation_lifecycle.state_for_key(conversation_key)

    def _new_conversation_state(self, conversation_key: str) -> TextConversationState:
        return TextConversationState(
            runtime=self._runtime_factory(conversation_key),
            busy_mode=self.default_busy_mode,
            route_binding=SessionRouteBinding(route=self),
            conversation_key=conversation_key,
        )

    async def _notify_busy_inbound(
        self,
        state: TextConversationState,
        inbound: InteractionInbound,
        decision: BusyInboundDecision,
    ) -> None:
        if decision.kind == "queue":
            await self._send_text(
                inbound.source,
                f"Queued for next turn: {self._shorten(inbound.text)}",
                reply_to=inbound.reply_to,
                metadata=inbound.metadata,
            )
            return
        if decision.kind == "interrupt":
            await self._send_text(
                inbound.source,
                f"Interrupting current turn; queued latest input: {self._shorten(inbound.text)}",
                reply_to=inbound.reply_to,
                metadata=inbound.metadata,
            )

    async def _run_inbound(self, state: TextConversationState, inbound: InteractionInbound) -> None:
        token = self._active_inbound.set(inbound)
        try:
            await self._send_typing(inbound)
            outbound = await state.runtime.handle(inbound, route_binding=state.route_binding)
            await self.deliver(outbound)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            context: dict[str, SecretValue] = {}
            provider = getattr(
                getattr(state.runtime, "runner", None),
                "provider",
                None,
            )
            provider_api_key = getattr(provider, "api_key", None)
            if isinstance(provider_api_key, str) and provider_api_key:
                context["api_key"] = SecretValue(
                    value=provider_api_key,
                    name="API_KEY",
                    source="provider.api_key",
                )
            for field_name in (
                "access_token",
                "api_key",
                "signing_secret",
                "token",
            ):
                value = getattr(self, field_name, None)
                if isinstance(value, str) and value:
                    context[field_name] = SecretValue(
                        value=value,
                        name=field_name.upper(),
                        source=f"channel.{field_name}",
                    )
            safe_error = redact_exception(
                exc,
                view=RedactionView.OPERATOR,
                context=context or None,
            )
            logger.error("%s turn failed: %s", self.channel_name, safe_error)
            await self._send_text(
                inbound.source,
                f"Turn failed: {safe_error}",
                reply_to=inbound.reply_to,
                metadata=inbound.metadata,
            )
        finally:
            self._active_inbound.reset(token)

    async def _handle_command(self, inbound: InteractionInbound, state: TextConversationState):
        async def send_notice(text: str) -> None:
            await self._send_command_text(inbound, text)

        return await self._command_runtime.handle(
            inbound,
            state,
            handlers=self._command_executor.handlers(),
            send_notice=send_notice,
        )

    async def _send_command_text(self, inbound: InteractionInbound, text: str) -> None:
        await self._send_text(inbound.source, text, reply_to=inbound.reply_to, metadata=inbound.metadata)

    async def _cancel_active(self, state: TextConversationState) -> None:
        await self._conversation_lifecycle.cancel_active(state)

    def _state_for_session(self, session_id: str) -> TextConversationState | None:
        return self._conversation_lifecycle.state_for_session(session_id)

    async def _send_typing(self, inbound: InteractionInbound) -> None:
        return None

    async def _send_text(
        self,
        source: str,
        text: str,
        *,
        reply_to: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Any:
        raise NotImplementedError

    def _shorten(self, text: str, *, limit: int = 80) -> str:
        compact = " ".join(text.split())
        if len(compact) <= limit:
            return compact
        return compact[: max(0, limit - 3)] + "..."


class ChannelRouterBridge:
    def __init__(self, bridges: dict[str, GatewayBridge], *, fallback: Callable[[str], GatewayBridge] | None = None) -> None:
        self.bridges = dict(bridges)
        self.fallback = fallback

    async def deliver(self, outbound: InteractionOutbound) -> None:
        bridge = self._bridge_for(outbound.channel)
        if bridge is None:
            raise RuntimeError(f"no delivery bridge registered for channel `{outbound.channel}`")
        await bridge.deliver(outbound)

    async def prompt_user(self, prompt: UserPromptRequest) -> str:
        channel = str(prompt.metadata.get("channel") or "")
        bridge = self._bridge_for(channel)
        if bridge is None:
            return ""
        return await bridge.prompt_user(prompt)

    async def request_approval(self, request: ApprovalRequest) -> ApprovalDecision:
        return ApprovalDecision("deny", "channel router cannot choose an approval bridge")

    def _bridge_for(self, channel: str) -> GatewayBridge | None:
        bridge = self.bridges.get(channel)
        if bridge is not None or self.fallback is None or not channel:
            return bridge
        bridge = self.fallback(channel)
        self.bridges[channel] = bridge
        return bridge


def resolve_env_value(env_name: str | None, inline_value: str | None) -> str | None:
    if env_name:
        import os

        value = os.environ.get(env_name)
        if value:
            return value
    return inline_value
