from __future__ import annotations

import asyncio
import contextlib
import contextvars
import json
import logging
from typing import Any, Callable, Protocol

from demiurge.channels.commands import ChannelCommandRuntime
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
from demiurge.runtime.session_commands import build_session_list_view, resolve_session_choice
from demiurge.runtime.runner import SessionTurnStepRunner
from demiurge.security.approval import ApprovalDecision, ApprovalRequest
from demiurge.providers import ToolCall
from demiurge.sdk import AgentInput, TurnContext
from demiurge.security.capabilities import CapabilityFacade


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

TEXT_CHANNEL_COMMAND_NAMES = frozenset(
    {"help", "status", "new", "stop", "queue", "busy", "sessions", "resume", "tools", "skills", "skill"}
)


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
        self.tool_display = _normalize_tool_display(tool_display)
        self._command_runtime = ChannelCommandRuntime(
            command_names=TEXT_CHANNEL_COMMAND_NAMES,
            unavailable_template="Command not available: /{name}",
            unknown_template="Unknown command: /{name}",
        )
        self._pending_choices: dict[str, list[str]] = {}
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
            pending_tool_results = []
            for item in outbound.items:
                if item.kind == "tool_call" and item.tool_call is not None:
                    if pending_tool_results:
                        await self._deliver_tool_results(pending_tool_results, outbound=outbound)
                        pending_tool_results = []
                    await self._deliver_tool_call(item.tool_call, outbound=outbound)
                    continue
                if item.kind == "tool_result" and item.tool_result is not None:
                    pending_tool_results.append(item.tool_result)
                    continue
                if item.kind == "delivery" and item.delivery is not None:
                    if pending_tool_results:
                        await self._deliver_tool_results(pending_tool_results, outbound=outbound)
                        pending_tool_results = []
                    await self._deliver_delivery(item.delivery, outbound=outbound)
            if pending_tool_results:
                await self._deliver_tool_results(pending_tool_results, outbound=outbound)
            if outbound.prompt is not None:
                await self.prompt_user(outbound.prompt)
        finally:
            outbound.mark_delivered()

    async def prompt_user(self, prompt: UserPromptRequest) -> str:
        if prompt.conversation_key and prompt.choices:
            self._pending_choices[prompt.conversation_key] = list(prompt.choices)
        source = prompt.metadata.get("source")
        if source is None:
            return ""
        reply_to = prompt.metadata.get("reply_to")
        await self._send_text(
            str(source),
            self._prompt_text(prompt),
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
        status = record.status
        if record.phase == "start":
            return f"## Tool call\n`{record.call.name}` - `running` - {self._shorten(_tool_call_start_summary(record.call), limit=220)}"
        result = ""
        if record.result is not None:
            result = self._shorten(record.result.display_output or record.result.content or "", limit=220)
        return f"## Tool call\n`{record.call.name}` - `{status}` - {result}"

    def _tool_results_text(self, records: list[Any]) -> str:
        if self.tool_display == "full":
            sections: list[str] = ["## Tool calls"]
            for index, record in enumerate(records, start=1):
                status = "error" if record.result.is_error else "ok"
                sections.extend(
                    [
                        "",
                        f"### {index}. `{record.call.name}` - `{status}`",
                        "",
                        "**Arguments**",
                        "```json",
                        self._shorten(json.dumps(record.call.arguments, ensure_ascii=False, indent=2), limit=1800),
                        "```",
                        "",
                        "**Result**",
                        "```",
                        self._shorten(record.result.display_output or record.result.content or "", limit=1800),
                        "```",
                    ]
                )
            return "\n".join(sections)
        lines = ["## Tool calls"]
        for index, record in enumerate(records, start=1):
            status = "error" if record.result.is_error else "ok"
            result = self._shorten(record.result.display_output or record.result.content or "", limit=220)
            lines.append(f"{index}. `{record.call.name}` - `{status}` - {result}")
        return "\n".join(lines)

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

    async def _drain_next_queued_input(self, state: TextConversationState) -> None:
        await ConversationTurnController(state).drain_next(
            lambda next_inbound: self._run_inbound(state, next_inbound)
        )

    async def _handle_command(self, inbound: InteractionInbound, state: TextConversationState):
        async def send_notice(text: str) -> None:
            await self._send_text(inbound.source, text, reply_to=inbound.reply_to, metadata=inbound.metadata)

        return await self._command_runtime.handle(
            inbound,
            state,
            handlers=self._command_handlers(),
            send_notice=send_notice,
        )

    def _command_handlers(self):
        return {
            "help": self._command_help,
            "status": self._command_status,
            "new": self._command_new,
            "stop": self._command_stop,
            "queue": self._command_queue,
            "busy": self._command_busy,
            "sessions": self._command_sessions,
            "resume": self._command_resume,
            "tools": self._command_tools,
            "skills": self._command_skills,
            "skill": self._command_skill,
        }

    async def _command_help(self, _: str, inbound: InteractionInbound, state: TextConversationState) -> None:
        await self._send_text(
            inbound.source,
            "# Commands\n- `/ask <prompt>` - send a prompt\n- `/status` - show runtime status\n- `/new` - start a new session\n- `/stop` - stop current work\n- `/queue <prompt>` - queue input\n- `/busy interrupt|queue` - set busy behavior\n- `/sessions [limit]` - list sessions\n- `/resume <number|session_id>` - resume a session\n- `/tools` - show tools\n- `/skills [category]` - show skills\n- `/skill <name>` - view a skill",
            reply_to=inbound.reply_to,
            metadata=inbound.metadata,
        )

    async def _command_status(self, _: str, inbound: InteractionInbound, state: TextConversationState) -> None:
        runner = state.runtime.runner
        queue_depth = state.queue.qsize()
        running = ConversationTurnController(state).running
        lines = [
            "# Status",
            f"- channel: `{self.channel_name}`",
            f"- core: `{getattr(runner, 'core_id', '?')}`",
            f"- session: `{getattr(runner, 'session_id', '?')}`",
            f"- running: `{str(running).lower()}`",
            f"- busy mode: `{state.busy_mode}`",
            f"- queued: `{queue_depth}`",
        ]
        session_id = getattr(runner, "session_id", None)
        if session_id:
            with contextlib.suppress(Exception):
                lines.append(f"- messages: `{state.runtime.session_runtime.message_count(session_id)}`")
        provider_name = getattr(runner, "provider_name", None)
        if provider_name:
            lines.append(f"- provider: `{provider_name}`")
        runtime_timezone = getattr(runner, "runtime_timezone", None)
        if runtime_timezone is not None:
            lines.append(f"- runtime timezone: `{runtime_timezone.name}` ({runtime_timezone.source})")
        await self._send_text(inbound.source, "\n".join(lines), reply_to=inbound.reply_to, metadata=inbound.metadata)

    async def _command_new(self, _: str, inbound: InteractionInbound, state: TextConversationState) -> None:
        await self._cancel_active(state)
        self._clear_queue(state, preserve_completions=False)
        runner = state.runtime.runner
        if not hasattr(runner, "start_new_session"):
            await self._send_text(inbound.source, "Session reset is not available.", reply_to=inbound.reply_to, metadata=inbound.metadata)
            return
        await runner.prepare_live_core()
        session_id = runner.start_new_session(
            channel=self.channel_name,
            conversation_key=inbound.conversation_key,
            source=inbound.source,
            reply_to=inbound.reply_to,
            replace_conversation_binding=True,
        )
        state.route_binding.bind(runner.interaction_router, session_id)
        await self._send_text(inbound.source, f"New session: `{session_id}`", reply_to=inbound.reply_to, metadata=inbound.metadata)

    async def _command_stop(self, _: str, inbound: InteractionInbound, state: TextConversationState) -> None:
        running = ConversationTurnController(state).running
        queued = self._clear_queue(state, preserve_completions=True)
        if running:
            await self._cancel_active(state)
            await self._send_text(
                inbound.source,
                f"Stopped current turn; cleared {queued} queued message(s).",
                reply_to=inbound.reply_to,
                metadata=inbound.metadata,
            )
            return
        await self._send_text(inbound.source, f"No running turn; cleared {queued} queued message(s).", reply_to=inbound.reply_to, metadata=inbound.metadata)
        await self._drain_next_queued_input(state)

    async def _command_queue(self, args: str, inbound: InteractionInbound, state: TextConversationState) -> None:
        text = args.strip()
        if not text:
            await self._send_text(inbound.source, "Usage: `/queue <prompt>`", reply_to=inbound.reply_to, metadata=inbound.metadata)
            return
        await ConversationTurnController(state).queue_and_drain_if_idle(
            InteractionInbound(
                channel=inbound.channel,
                text=text,
                source=inbound.source,
                reply_to=inbound.reply_to,
                conversation_key=inbound.conversation_key,
                metadata=dict(inbound.metadata),
            ),
            lambda next_inbound: self._run_inbound(state, next_inbound),
        )
        await self._send_text(inbound.source, f"Queued: {self._shorten(text)}", reply_to=inbound.reply_to, metadata=inbound.metadata)

    async def _command_busy(self, args: str, inbound: InteractionInbound, state: TextConversationState) -> None:
        mode = args.strip().lower()
        if not mode:
            await self._send_text(inbound.source, f"Busy mode: `{state.busy_mode}`", reply_to=inbound.reply_to, metadata=inbound.metadata)
            return
        if mode not in {"interrupt", "queue"}:
            await self._send_text(inbound.source, "Usage: `/busy interrupt|queue`", reply_to=inbound.reply_to, metadata=inbound.metadata)
            return
        state.busy_mode = mode
        await self._send_text(inbound.source, f"Busy mode: `{state.busy_mode}`", reply_to=inbound.reply_to, metadata=inbound.metadata)

    async def _command_sessions(self, args: str, inbound: InteractionInbound, state: TextConversationState) -> None:
        limit = int(args.strip()) if args.strip().isdigit() else 10
        view = build_session_list_view(
            state.runtime.session_runtime,
            core_id=state.runtime.runner.core_id,
            active_session_id=state.runtime.runner.session_id,
            limit=limit,
        )
        await self._send_text(inbound.source, view.text(), reply_to=inbound.reply_to, metadata=inbound.metadata)

    async def _command_resume(self, args: str, inbound: InteractionInbound, state: TextConversationState) -> None:
        raw = args.strip()
        view = build_session_list_view(
            state.runtime.session_runtime,
            core_id=state.runtime.runner.core_id,
            active_session_id=state.runtime.runner.session_id,
            limit=20,
        )
        if not raw:
            await self._send_text(
                inbound.source,
                view.text() + "\n\nUse `/resume <number|session_id>`.",
                reply_to=inbound.reply_to,
                metadata=inbound.metadata,
            )
            return
        resolution = resolve_session_choice(raw, view)
        if not resolution.ok:
            await self._send_text(
                inbound.source,
                resolution.message or "Invalid session selection.",
                reply_to=inbound.reply_to,
                metadata=inbound.metadata,
            )
            return
        assert resolution.session_id is not None
        try:
            state.runtime.runner.resume_session(resolution.session_id)
        except FileNotFoundError as exc:
            await self._send_text(inbound.source, str(exc), reply_to=inbound.reply_to, metadata=inbound.metadata)
            return
        state.route_binding.bind(state.runtime.runner.interaction_router, resolution.session_id)
        await self._send_text(inbound.source, f"Resumed session: `{resolution.session_id}`", reply_to=inbound.reply_to, metadata=inbound.metadata)

    async def _command_tools(self, _: str, inbound: InteractionInbound, state: TextConversationState) -> None:
        runner = state.runtime.runner
        core = await runner.load_active_core()
        lines = ["# Tools"]
        for entry in runner.tool_runtime.registry_for(core):
            lines.append(f"- `{entry.name}` - {entry.source} - {entry.approval_policy}")
        await self._send_text(inbound.source, "\n".join(lines), reply_to=inbound.reply_to, metadata=inbound.metadata)

    async def _command_skills(self, args: str, inbound: InteractionInbound, state: TextConversationState) -> None:
        runner = state.runtime.runner
        core = await runner.load_active_core()
        category = args.strip() or None
        skills = [skill for skill in core.skills if category is None or skill.category == category]
        lines = ["# Skills"]
        for skill in skills:
            lines.append(f"- `{skill.name}` - {skill.category} - {skill.description}")
        await self._send_text(inbound.source, "\n".join(lines), reply_to=inbound.reply_to, metadata=inbound.metadata)

    async def _command_skill(self, args: str, inbound: InteractionInbound, state: TextConversationState) -> None:
        parts = args.split(maxsplit=1)
        if not parts:
            await self._send_text(inbound.source, "Usage: `/skill <name>`", reply_to=inbound.reply_to, metadata=inbound.metadata)
            return
        runner = state.runtime.runner
        core = await runner.load_active_core()
        result = await runner.tool_runtime.execute(
            ToolCall(name="skill_view", arguments={"name": parts[0]}, id=f"{self.channel_name}_skill_view"),
            core=core,
            turn=TurnContext(
                session_id=runner.session_id,
                turn_id=f"{self.channel_name}_slash",
                core_id=core.core_id,
                core_revision=runner.version_store.active_pointer(core.core_id).active_revision,
                user_input=AgentInput(content=inbound.text, metadata=dict(inbound.metadata)),
                metadata=dict(inbound.metadata),
            ),
            capability=CapabilityFacade(core),
            emit_event=runner.event_log.emit,
        )
        content = result.content
        if isinstance(result.data, dict) and result.data.get("content"):
            content = str(result.data["content"])
        await self._send_text(inbound.source, content[:3800], reply_to=inbound.reply_to, metadata=inbound.metadata)

    async def _cancel_active(self, state: TextConversationState) -> None:
        await ConversationTurnController(state).cancel_active()

    def _clear_queue(self, state: TextConversationState, *, preserve_completions: bool) -> int:
        return ConversationTurnController(state).clear_queue(preserve_completions=preserve_completions)

    def _remember_route(self, state: TextConversationState, inbound: InteractionInbound) -> None:
        state.remember_route(inbound)

    def _merge_stored_task_completions(
        self,
        state: TextConversationState,
        inbound: InteractionInbound,
    ) -> InteractionInbound:
        completions = state.claim_pending_completions(
            channel=self.channel_name,
            owner_id=f"bridge:{self.channel_name}:merge",
            fallback_source=inbound.source,
        )
        if not completions:
            return inbound
        return state.queue.merge_completions_into(inbound, stored_completions=completions)

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
        if not state.source:
            return
        if self._task_worker is None:
            return
        inbound = state.claim_completion_event(
            event,
            channel=self.channel_name,
            owner_id=f"bridge:{self.channel_name}:enqueue",
            task_worker=self._task_worker,
        )
        if inbound is None:
            return
        await ConversationTurnController(state).enqueue_completion(
            inbound,
            lambda next_inbound: self._run_inbound(state, next_inbound),
        )

    def _state_for_session(self, session_id: str) -> TextConversationState | None:
        for state in self._conversations.values():
            if state.session_id == session_id:
                return state
        return None

    def _consume_inbound_pending_choice(self, inbound: InteractionInbound) -> InteractionInbound:
        if not inbound.conversation_key:
            return inbound
        text = self._consume_pending_choice(inbound.conversation_key, inbound.text.strip())
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

    def _consume_pending_choice(self, conversation_key: str, text: str) -> str:
        choices = self._pending_choices.get(conversation_key)
        if not choices:
            return text
        value = text.strip()
        self._pending_choices.pop(conversation_key, None)
        if value.isdigit():
            index = int(value) - 1
            if 0 <= index < len(choices):
                return choices[index]
        return text

    def _prompt_text(self, prompt: UserPromptRequest) -> str:
        lines = [prompt.question]
        for index, choice in enumerate(prompt.choices, start=1):
            lines.append(f"{index}. {choice}")
        return "\n".join(lines)

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


def _tool_call_start_summary(call: ToolCall) -> str:
    if call.name == "terminal":
        command = str(call.arguments.get("command") or "").strip()
        return f"$ {command}" if command else "running terminal"
    if call.name in {"read_file", "write_file", "patch"}:
        path = call.arguments.get("path") or call.arguments.get("file_path")
        return f"{call.name}: {path}" if path else call.name
    if call.arguments:
        return json.dumps(call.arguments, ensure_ascii=False, sort_keys=True)
    return "running"


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


def _normalize_tool_display(value: str | None) -> str:
    normalized = (value or "summary").strip().lower()
    return normalized if normalized in {"quiet", "summary", "full"} else "summary"


def _media_block_fallback(block: dict[str, Any]) -> str:
    artifact = block.get("artifact")
    if not isinstance(artifact, dict):
        return ""
    summary = artifact.get("summary") or artifact.get("media_type") or artifact.get("kind") or block.get("type")
    artifact_id = artifact.get("artifact_id") or "artifact"
    caption = block.get("text")
    prefix = f"{caption}\n" if caption else ""
    return f"{prefix}[artifact:{artifact_id} {artifact.get('kind') or block.get('type')} {summary}]"
