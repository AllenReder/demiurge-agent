from __future__ import annotations

import asyncio
import contextlib
import contextvars
import logging
from typing import Any, Callable, Protocol

from demiurge.channels.commands import ChannelCommandExecutor, ChannelCommandRuntime
from demiurge.runtime.completions import is_background_completion
from demiurge.runtime.tasks import RuntimeTaskCompletionEvent, RuntimeTaskWorker
from demiurge.runtime.interactions import (
    InteractionDelivery,
    InteractionInbound,
    InteractionOutbound,
    InteractionRuntime,
    SessionRouteBinding,
    ToolInteractionRecord,
    UserPromptRequest,
)
from demiurge.runtime.ingress import ConversationIngressState, ConversationTurnController
from demiurge.runtime.outbound_delivery import text_delivery_steps
from demiurge.runtime.prompts import PromptChoiceRuntime, format_prompt_text
from demiurge.runtime.runner import SessionTurnStepRunner
from demiurge.runtime.tool_display import normalize_tool_display, tool_call_markdown, tool_results_markdown
from demiurge.security.approval import ApprovalDecision, ApprovalRequest
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
        self._command_executor = ChannelCommandExecutor(
            channel_name=channel_name,
            surface="text",
            send_text=self._send_command_text,
            run_inbound=self._run_inbound,
            help_extra_lines=("- `/ask <prompt>` - send a prompt",),
        )
        self._pending_choices = PromptChoiceRuntime()
        self._conversations: dict[str, TextConversationState] = {}
        self._task_worker: RuntimeTaskWorker | None = None
        self._task_unsubscribe: Callable[[], None] | None = None
        self._active_inbound: contextvars.ContextVar[InteractionInbound | None] = contextvars.ContextVar(
            f"demiurge_{channel_name}_active_inbound",
            default=None,
        )

    async def run_forever(self) -> None:
        raise NotImplementedError

    async def handle_inbound(self, inbound: InteractionInbound) -> None:
        state = self._conversation_state(inbound.conversation_key or f"{self.channel_name}:{inbound.source}")
        self._remember_route(state, inbound)
        command_outcome = await self._handle_command(inbound, state)
        if command_outcome.handled:
            return
        inbound = command_outcome.inbound

        if inbound.conversation_key:
            inbound = self._consume_inbound_pending_choice(inbound)
        if not is_background_completion(inbound):
            inbound = self._merge_stored_task_completions(state, inbound)
        if state.active_task and not state.active_task.done():
            await self._handle_busy_inbound(state, inbound)
            return
        self._start_turn(state, inbound)

    async def deliver(self, outbound: InteractionOutbound) -> None:
        try:
            for step in text_delivery_steps(outbound):
                if step.kind == "tool_call" and step.tool_call is not None:
                    await self._deliver_tool_call(step.tool_call, outbound=outbound)
                    continue
                if step.kind == "tool_results":
                    await self._deliver_tool_results(list(step.tool_results), outbound=outbound)
                    continue
                if step.kind == "delivery" and step.deliveries:
                    await self._deliver_delivery(step.deliveries[0], outbound=outbound)
                    continue
                if step.kind == "prompt" and step.prompt is not None:
                    await self.prompt_user(step.prompt)
        finally:
            outbound.mark_delivered()

    async def prompt_user(self, prompt: UserPromptRequest) -> str:
        self._pending_choices.remember(prompt.conversation_key, prompt.choices)
        source = prompt.metadata.get("source")
        if source is None:
            return ""
        reply_to = prompt.metadata.get("reply_to")
        await self._send_text(
            str(source),
            format_prompt_text(prompt.question, prompt.choices),
            reply_to=str(reply_to) if reply_to is not None else None,
            metadata=prompt.metadata,
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
        source = outbound.metadata.get("source")
        if source is None:
            return
        reply_to = outbound.metadata.get("reply_to")
        chunks = self._delivery_text_chunks(delivery)
        for index, chunk in enumerate(chunks):
            await self._send_text(
                str(source),
                chunk,
                reply_to=str(reply_to) if reply_to is not None and index == 0 else None,
                metadata=outbound.metadata,
            )

    def _delivery_text_chunks(self, delivery: InteractionDelivery) -> list[str]:
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
            fallback = _media_block_fallback(block)
            if fallback:
                chunks.append(fallback)
        if not chunks and (delivery.text or delivery.fallback_text):
            chunks.append(delivery.text or delivery.fallback_text)
        return chunks

    async def _deliver_tool_results(self, records: list[Any], *, outbound: InteractionOutbound) -> None:
        if self.tool_display == "quiet" or not records:
            return
        source = outbound.metadata.get("source")
        if source is None:
            return
        reply_to = outbound.metadata.get("reply_to")
        text = self._tool_results_text(records)
        if text:
            await self._send_text(
                str(source),
                text,
                reply_to=str(reply_to) if reply_to is not None else None,
                metadata=outbound.metadata,
            )

    async def _deliver_tool_call(self, record: ToolInteractionRecord, *, outbound: InteractionOutbound) -> None:
        if self.tool_display == "quiet":
            return
        source = outbound.metadata.get("source")
        if source is None:
            return
        reply_to = outbound.metadata.get("reply_to")
        text = self._tool_call_text(record)
        if text:
            await self._send_text(
                str(source),
                text,
                reply_to=str(reply_to) if reply_to is not None else None,
                metadata=outbound.metadata,
            )

    def _tool_call_text(self, record: ToolInteractionRecord) -> str:
        return tool_call_markdown(record, mode=self.tool_display)

    def _tool_results_text(self, records: list[Any]) -> str:
        return tool_results_markdown(records, mode=self.tool_display)

    def _conversation_state(self, conversation_key: str) -> TextConversationState:
        state = self._conversations.get(conversation_key)
        if state is None:
            state = TextConversationState(
                runtime=self._runtime_factory(conversation_key),
                busy_mode=self.default_busy_mode,
                route_binding=SessionRouteBinding(route=self),
                conversation_key=conversation_key,
            )
            self._conversations[conversation_key] = state
            self._subscribe_task_worker(state.runtime)
        return state

    async def _handle_busy_inbound(self, state: TextConversationState, inbound: InteractionInbound) -> None:
        async def notify(decision) -> None:
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

        await ConversationTurnController(state).handle_busy_inbound(inbound, notify=notify)

    def _start_turn(self, state: TextConversationState, inbound: InteractionInbound) -> None:
        ConversationTurnController(state).start(inbound, lambda next_inbound: self._run_inbound(state, next_inbound))

    async def _run_inbound(self, state: TextConversationState, inbound: InteractionInbound) -> None:
        task = asyncio.current_task()
        token = self._active_inbound.set(inbound)
        try:
            await self._send_typing(inbound)
            outbound = await state.runtime.handle(inbound, route_binding=state.route_binding)
            await self.deliver(outbound)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("%s turn failed", self.channel_name)
            await self._send_text(inbound.source, f"Turn failed: {exc}", reply_to=inbound.reply_to, metadata=inbound.metadata)
        finally:
            self._active_inbound.reset(token)
            controller = ConversationTurnController(state)
            controller.finish(task)
            await controller.drain_next(lambda next_inbound: self._run_inbound(state, next_inbound))

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

    def _remember_route(self, state: TextConversationState, inbound: InteractionInbound) -> None:
        state.remember_route(inbound)

    def _merge_stored_task_completions(
        self,
        state: TextConversationState,
        inbound: InteractionInbound,
    ) -> InteractionInbound:
        return ConversationTurnController(state).merge_pending_completions(
            inbound,
            channel=self.channel_name,
            owner_id=f"bridge:{self.channel_name}:merge",
            fallback_source=inbound.source,
        )

    def _subscribe_task_worker(self, runtime: InteractionRuntime) -> None:
        task_worker = getattr(getattr(runtime, "runner", None), "task_worker", None)
        if task_worker is None or task_worker is self._task_worker:
            return
        if self._task_unsubscribe is not None:
            self._task_unsubscribe()
        self._task_worker = task_worker
        self._task_unsubscribe = task_worker.subscribe(self._on_task_completion)

    def _on_task_completion(self, event: RuntimeTaskCompletionEvent) -> None:
        state = self._state_for_session(event.owner_session_id)
        if state is None:
            return
        try:
            asyncio.get_running_loop().create_task(self._enqueue_task_completion(state, event))
        except RuntimeError:
            return

    async def _enqueue_task_completion(self, state: TextConversationState, event: RuntimeTaskCompletionEvent) -> None:
        await ConversationTurnController(state).enqueue_completion_event(
            event,
            channel=self.channel_name,
            owner_id=f"bridge:{self.channel_name}:enqueue",
            run=lambda next_inbound: self._run_inbound(state, next_inbound),
            task_worker=self._task_worker,
            require_source=True,
        )

    def _state_for_session(self, session_id: str) -> TextConversationState | None:
        for state in self._conversations.values():
            if state.session_id == session_id:
                return state
        return None

    def _consume_inbound_pending_choice(self, inbound: InteractionInbound) -> InteractionInbound:
        if not inbound.conversation_key:
            return inbound
        text = self._pending_choices.consume_text(inbound.conversation_key, inbound.text.strip()).text
        if text == inbound.text:
            return inbound
        return InteractionInbound(
            channel=inbound.channel,
            text=text,
            source=inbound.source,
            reply_to=inbound.reply_to,
            conversation_key=inbound.conversation_key,
            metadata=dict(inbound.metadata),
        )

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


def runtime_factory_for_app(app: Any) -> Callable[[str], InteractionRuntime]:
    if not all(hasattr(app, name) for name in ("home", "version_store", "core_loader", "tool_runtime")):
        runtime = InteractionRuntime(app.runner)
        return lambda _conversation_key: runtime

    def make_runtime(_conversation_key: str) -> InteractionRuntime:
        runner = SessionTurnStepRunner(
            home=app.home,
            version_store=app.version_store,
            core_loader=app.core_loader,
            provider=app.runner.provider,
            tool_runtime=app.tool_runtime,
            core_id=app.runner.core_id,
            model_override=app.runner.model_override,
            model_resolver=app.runner.model_resolver,
            provider_name=app.runner.provider_name,
            workspace=app.runner.workspace,
            show_system_prompt=app.runner.show_system_prompt,
            runtime_timezone=app.runtime_timezone,
            task_worker=app.task_worker,
            session_runtime=app.session_runtime,
            interaction_router=app.runner.interaction_router,
            prepare_live_core=app.prepare_live_core,
        )
        return InteractionRuntime(runner)

    return make_runtime


def resolve_env_value(env_name: str | None, inline_value: str | None) -> str | None:
    if env_name:
        import os

        value = os.environ.get(env_name)
        if value:
            return value
    return inline_value


def _media_block_fallback(block: dict[str, Any]) -> str:
    artifact = block.get("artifact")
    if not isinstance(artifact, dict):
        return ""
    summary = artifact.get("summary") or artifact.get("media_type") or artifact.get("kind") or block.get("type")
    artifact_id = artifact.get("artifact_id") or "artifact"
    caption = block.get("text")
    prefix = f"{caption}\n" if caption else ""
    return f"{prefix}[artifact:{artifact_id} {artifact.get('kind') or block.get('type')} {summary}]"
