from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from demiurge.providers import ToolCall
from demiurge.runtime.delegation import subagents_command_text
from demiurge.runtime.conversation_lifecycle import ConversationLifecycleRuntime
from demiurge.runtime.interactions import InteractionInbound
from demiurge.runtime.session_commands import (
    build_session_list_view,
    resolve_session_choice,
    resume_bound_session,
    start_bound_session,
)
from demiurge.runtime.status_commands import build_runtime_status_view, format_runtime_status_markdown
from demiurge.sdk import AgentInput, TurnContext
from demiurge.security.capabilities import CapabilityFacade
from demiurge.slash import SlashCommand, help_text_for_surface, parse_slash_command


CommandHandler = Callable[[str, InteractionInbound, Any], Awaitable[None]]
NoticeSender = Callable[[str], Awaitable[None]]
CommandTextSender = Callable[[InteractionInbound, str], Awaitable[None]]
CommandCancelActive = Callable[[Any], Awaitable[None]]
CommandStatusExtraLines = Callable[[InteractionInbound], Iterable[str]]


@dataclass(frozen=True, slots=True)
class ChannelCommandOutcome:
    handled: bool
    inbound: InteractionInbound
    command: SlashCommand | None = None


@dataclass(slots=True)
class ChannelCommandExecutor:
    """Shared runtime command implementation for external channel adapters."""

    channel_name: str
    surface: str
    send_text: CommandTextSender
    lifecycle: ConversationLifecycleRuntime
    cancel_active: CommandCancelActive | None = None
    help_extra_lines: tuple[str, ...] = ()
    include_status_channel: bool = True
    status_extra_lines: CommandStatusExtraLines | None = None
    include_subagents: bool = False
    text_limit: int | None = 3800
    _handlers: dict[str, CommandHandler] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        handlers: dict[str, CommandHandler] = {
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
        if self.include_subagents:
            handlers["subagents"] = self._command_subagents
        self._handlers = handlers

    def handlers(self) -> Mapping[str, CommandHandler]:
        return self._handlers

    async def _send(self, inbound: InteractionInbound, text: str, *, limit: bool = False) -> None:
        if limit and self.text_limit is not None:
            text = text[: self.text_limit]
        await self.send_text(inbound, text)

    async def _command_help(self, _: str, inbound: InteractionInbound, state: Any) -> None:
        await self._send(
            inbound,
            help_text_for_surface(self.surface, extra_lines=self.help_extra_lines),
        )

    async def _command_status(self, _: str, inbound: InteractionInbound, state: Any) -> None:
        session_runtime = getattr(state.runtime, "session_runtime", None) or getattr(
            state.runtime.runner,
            "session_runtime",
            None,
        )
        extra_lines = tuple(self.status_extra_lines(inbound)) if self.status_extra_lines is not None else ()
        view = build_runtime_status_view(
            state.runtime.runner,
            session_runtime,
            running=self.lifecycle.running(state),
            busy_mode=state.busy_mode,
            queued_inputs=self.lifecycle.queued_count(state),
            channel=self.channel_name if self.include_status_channel else None,
        )
        await self._send(inbound, format_runtime_status_markdown(view, extra_lines=extra_lines))

    async def _command_new(self, _: str, inbound: InteractionInbound, state: Any) -> None:
        await self._cancel_active(state)
        self.lifecycle.clear_queue(state, preserve_completions=False)
        result = await start_bound_session(
            state.runtime.runner,
            state.route_binding,
            channel=self.channel_name,
            conversation_key=inbound.conversation_key,
            principal_key=inbound.principal_key,
            source=inbound.source,
            reply_to=inbound.reply_to,
            replace_conversation_binding=True,
        )
        if not result.ok:
            await self._send(inbound, result.message)
            return
        await self._send(inbound, f"New session: `{result.session_id}`")

    async def _command_stop(self, _: str, inbound: InteractionInbound, state: Any) -> None:
        running = self.lifecycle.running(state)
        queued = self.lifecycle.clear_queue(state, preserve_completions=True)
        if running:
            await self._cancel_active(state)
            await self._send(inbound, f"Stopped current turn; cleared {queued} queued message(s).")
            return
        await self._send(inbound, f"No running turn; cleared {queued} queued message(s).")
        await self.lifecycle.drain_next(state)

    async def _command_queue(self, args: str, inbound: InteractionInbound, state: Any) -> None:
        text = args.strip()
        if not text:
            await self._send(inbound, "Usage: `/queue <prompt>`")
            return
        await self.lifecycle.queue_and_drain_if_idle(
            state,
            InteractionInbound(
                channel=inbound.channel,
                text=text,
                source=inbound.source,
                principal_key=inbound.principal_key,
                reply_to=inbound.reply_to,
                conversation_key=inbound.conversation_key,
                metadata=dict(inbound.metadata),
            ),
        )
        await self._send(inbound, f"Queued: {_shorten(text)}")

    async def _command_busy(self, args: str, inbound: InteractionInbound, state: Any) -> None:
        mode = args.strip().lower()
        if not mode:
            await self._send(inbound, f"Busy mode: `{state.busy_mode}`")
            return
        if mode not in {"interrupt", "queue"}:
            await self._send(inbound, "Usage: `/busy interrupt|queue`")
            return
        state.busy_mode = mode
        await self._send(inbound, f"Busy mode: `{state.busy_mode}`")

    async def _command_sessions(self, args: str, inbound: InteractionInbound, state: Any) -> None:
        limit = int(args.strip()) if args.strip().isdigit() else 10
        view = build_session_list_view(
            state.runtime.session_runtime,
            core_id=state.runtime.runner.core_id,
            active_session_id=state.runtime.runner.session_id,
            limit=limit,
        )
        await self._send(inbound, view.text())

    async def _command_resume(self, args: str, inbound: InteractionInbound, state: Any) -> None:
        raw = args.strip()
        view = build_session_list_view(
            state.runtime.session_runtime,
            core_id=state.runtime.runner.core_id,
            active_session_id=state.runtime.runner.session_id,
            limit=20,
        )
        if not raw:
            await self._send(inbound, view.text() + "\n\nUse `/resume <number|session_id>`.")
            return
        resolution = resolve_session_choice(raw, view)
        if not resolution.ok:
            await self._send(inbound, resolution.message or "Invalid session selection.")
            return
        assert resolution.session_id is not None
        result = resume_bound_session(
            state.runtime.runner,
            state.route_binding,
            resolution.session_id,
            channel=self.channel_name,
            conversation_key=inbound.conversation_key,
            principal_key=inbound.principal_key,
            source=inbound.source,
            reply_to=inbound.reply_to,
            replace_conversation_binding=True,
        )
        if not result.ok:
            await self._send(inbound, result.message)
            return
        await self._send(inbound, f"Resumed session: `{result.session_id}`")

    async def _command_tools(self, _: str, inbound: InteractionInbound, state: Any) -> None:
        runner = state.runtime.runner
        core = await runner.load_active_core()
        lines = ["# Tools"]
        for entry in runner.tool_runtime.registry_for(core):
            lines.append(f"- `{entry.name}` - {entry.source} - {entry.approval_policy}")
        await self._send(inbound, "\n".join(lines))

    async def _command_skills(self, args: str, inbound: InteractionInbound, state: Any) -> None:
        runner = state.runtime.runner
        core = await runner.load_active_core()
        category = args.strip() or None
        skills = [skill for skill in core.skills if category is None or skill.category == category]
        lines = ["# Skills"]
        for skill in skills:
            lines.append(f"- `{skill.name}` - {skill.category} - {skill.description}")
        await self._send(inbound, "\n".join(lines))

    async def _command_skill(self, args: str, inbound: InteractionInbound, state: Any) -> None:
        parts = args.split(maxsplit=1)
        if not parts:
            await self._send(inbound, "Usage: `/skill <name>`")
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
            principal_scope=runner.principal_scope,
            emit_event=runner.event_log.emit,
        )
        content = result.content
        if isinstance(result.data, dict) and result.data.get("content"):
            content = str(result.data["content"])
        await self._send(inbound, content, limit=True)

    async def _command_subagents(self, args: str, inbound: InteractionInbound, state: Any) -> None:
        text = await subagents_command_text(
            state.runtime.runner.task_worker,
            session_id=state.runtime.runner.session_id,
            args=args,
        )
        await self._send(inbound, text, limit=True)

    async def _cancel_active(self, state: Any) -> None:
        if self.cancel_active is not None:
            await self.cancel_active(state)
            return
        await self.lifecycle.cancel_active(state)


class ChannelCommandRuntime:
    """Command classification and dispatch for external channel adapters."""

    def __init__(
        self,
        *,
        command_names: set[str] | frozenset[str],
        unavailable_template: str,
        unknown_template: str,
    ) -> None:
        self.command_names = frozenset(command_names)
        self.unavailable_template = unavailable_template
        self.unknown_template = unknown_template

    async def handle(
        self,
        inbound: InteractionInbound,
        state: Any,
        *,
        handlers: Mapping[str, CommandHandler],
        send_notice: NoticeSender,
    ) -> ChannelCommandOutcome:
        command = parse_slash_command(inbound.text)
        if command is None:
            return ChannelCommandOutcome(handled=False, inbound=inbound)
        if command.name == "ask" and command.args:
            return ChannelCommandOutcome(
                handled=False,
                inbound=InteractionInbound(
                    channel=inbound.channel,
                    text=command.args,
                    source=inbound.source,
                    principal_key=inbound.principal_key,
                    reply_to=inbound.reply_to,
                    conversation_key=inbound.conversation_key,
                    metadata=dict(inbound.metadata),
                ),
                command=command,
            )
        if command.name in self.command_names:
            handler = handlers.get(command.name)
            if handler is None:
                await send_notice(self.unavailable_template.format(name=command.name))
                return ChannelCommandOutcome(handled=True, inbound=inbound, command=command)
            await handler(command.args, inbound, state)
            return ChannelCommandOutcome(handled=True, inbound=inbound, command=command)
        await send_notice(self.unknown_template.format(name=command.name))
        return ChannelCommandOutcome(handled=True, inbound=inbound, command=command)


def _shorten(text: str, *, limit: int = 80) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: max(0, limit - 3)] + "..."
