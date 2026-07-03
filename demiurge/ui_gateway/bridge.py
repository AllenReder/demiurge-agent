from __future__ import annotations

import asyncio
import contextlib
import json
from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any, Awaitable, Callable

from demiurge.app import DemiurgeApp, load_host_config
from demiurge.diagnostics.doctor import DoctorRuntime
from demiurge.runtime.tasks import RuntimeTaskCompletionEvent
from demiurge.packages import PackageManager, PackageOperationError, load_package_repository_collection
from demiurge.providers import ToolCall
from demiurge.runtime.delegation import subagents_command_text
from demiurge.runtime.interactions import InteractionInbound, InteractionOutbound, InteractionRuntime, UserPromptRequest
from demiurge.sdk import AgentInput, TurnContext
from demiurge.scheduler import SchedulerService, start_scheduler_for_app
from demiurge.security.approval import ApprovalDecision, ApprovalRequest
from demiurge.security.capabilities import CapabilityFacade
from demiurge.slash import SLASH_COMMANDS, SlashCommand, SlashCommandSpec, parse_slash_command, specs_for_surface
from demiurge.storage import EventLog, SessionRecord
from demiurge.tools.records import ToolExecutionRecord
from demiurge.ui_gateway.protocol import JsonEventSink


@dataclass(slots=True)
class PendingPrompt:
    prompt_id: str
    question: str
    choices: list[str] = field(default_factory=list)
    kind: str = "clarify"
    future: asyncio.Future[str] | None = None
    records: list[SessionRecord] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


def parse_approval_response(text: str) -> ApprovalDecision:
    normalized = text.strip().lower()
    if normalized in {"1", "y", "yes", "allow", "approve", "once"}:
        return ApprovalDecision("allow", "approved by TUI user")
    if normalized in {"2", "a", "always", "session", "always_allow_for_session"}:
        return ApprovalDecision("always_allow_for_session", "approved by TUI user for this session")
    if normalized in {"3", "n", "no", "deny", ""}:
        return ApprovalDecision("deny", "denied by TUI user")
    return ApprovalDecision("deny", f"invalid approval input: {text}")


def parse_tool_display_level(text: str) -> str | None:
    normalized = text.strip().lower()
    aliases = {
        "off": "quiet",
        "none": "quiet",
        "hidden": "quiet",
        "quiet": "quiet",
        "summary": "summary",
        "brief": "summary",
        "full": "full",
        "verbose": "full",
    }
    return aliases.get(normalized)


class TuiInteractionBridge:
    """Local TUI channel adapter.

    This class is intentionally the Python-side channel adapter, mirroring
    TelegramInteractionBridge's boundary. TypeScript renders the terminal and
    sends interaction intents; this class owns the conversion to
    InteractionInbound/InteractionOutbound and implements InteractionBridge.
    """

    def __init__(
        self,
        app: DemiurgeApp,
        *,
        emit: JsonEventSink,
        tool_display: str | None = None,
        busy_mode: str | None = None,
    ):
        self.app = app
        self.emit = emit
        self.runtime = InteractionRuntime(app.runner)
        self.tool_display = parse_tool_display_level(tool_display or app.tool_display) or "summary"
        resolved_busy_mode = busy_mode or app.channel_busy_mode
        self.busy_mode = resolved_busy_mode if resolved_busy_mode in {"interrupt", "queue"} else "interrupt"
        self._running_task: asyncio.Task[None] | None = None
        self._queued_inputs: asyncio.Queue[InteractionInbound] = asyncio.Queue()
        self._pending_prompts: dict[str, PendingPrompt] = {}
        self._pending_approvals: dict[str, asyncio.Future[ApprovalDecision]] = {}
        self._prompt_counter = 0
        self._approval_counter = 0
        self._last_error = ""
        self.should_exit = False
        self._scheduler: SchedulerService | None = None
        self._task_unsubscribe = self.app.task_worker.subscribe(self._on_task_completion)

    @property
    def running(self) -> bool:
        return self._running_task is not None and not self._running_task.done()

    async def initialize(self) -> dict[str, Any]:
        self._start_scheduler()
        await self._emit_ready()
        if self.app.session_runtime.message_count(self.app.runner.session_id):
            await self._emit_history_snapshot(self.app.runner.session_id)
        await self._emit_status()
        return self._status_payload()

    async def submit(self, text: str) -> dict[str, Any]:
        text = str(text or "").strip()
        if not text:
            return {"accepted": False, "reason": "empty"}
        inbound = self._user_inbound(text)
        if self.running:
            if self.busy_mode == "queue":
                await self._queued_inputs.put(inbound)
                await self._emit_notice(f"queued input: {shorten_text(text, 100)}")
                await self._emit_status()
                return {"accepted": True, "queued": True}
            await self._queued_inputs.put(inbound)
            await self.interrupt_current_turn(reason="new input")
            return {"accepted": True, "queued": True}
        inbound = self._merge_pending_completions_into(inbound)
        self._running_task = asyncio.create_task(self._run_inbound(inbound))
        await self._emit_status()
        return {"accepted": True, "queued": False}

    async def wait_for_idle(self) -> None:
        task = self._running_task
        if task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def deliver(self, outbound: InteractionOutbound) -> None:
        try:
            pending_tool_results = []
            pending_deliveries = []
            for item in outbound.items:
                if item.kind == "tool_result" and item.tool_result is not None:
                    if pending_deliveries:
                        await self._emit_deliveries(outbound, pending_deliveries)
                        pending_deliveries = []
                    pending_tool_results.append(item.tool_result)
                    continue
                if item.kind == "delivery" and item.delivery is not None:
                    if pending_tool_results:
                        await self._emit_tool_results(pending_tool_results)
                        pending_tool_results = []
                    pending_deliveries.append(item.delivery)
            if pending_tool_results:
                await self._emit_tool_results(pending_tool_results)
            if pending_deliveries:
                await self._emit_deliveries(outbound, pending_deliveries)
            if outbound.prompt is not None:
                await self._open_prompt(outbound.prompt, kind="clarify", wait=False)
        finally:
            outbound.mark_delivered()
            await self._emit_status()

    async def _emit_deliveries(self, outbound: InteractionOutbound, deliveries) -> None:
        await self.emit(
            "interaction.deliver",
            {
                "channel": outbound.channel,
                "session_id": outbound.session_id,
                "turn_id": outbound.turn_id,
                "metadata": _json_safe(outbound.metadata),
                "deliveries": [_delivery_dict(delivery) for delivery in deliveries],
            },
        )

    async def prompt_user(self, prompt: UserPromptRequest) -> str:
        pending = await self._open_prompt(prompt, kind=str(prompt.metadata.get("kind") or "clarify"), wait=True)
        assert pending.future is not None
        return await pending.future

    async def request_approval(self, request: ApprovalRequest) -> ApprovalDecision:
        self._approval_counter += 1
        approval_id = f"approval_{self._approval_counter}"
        loop = asyncio.get_running_loop()
        future: asyncio.Future[ApprovalDecision] = loop.create_future()
        self._pending_approvals[approval_id] = future
        await self.emit("interaction.approval.request", {"approval_id": approval_id, "request": asdict(request)})
        await self._emit_status()
        try:
            return await future
        finally:
            self._pending_approvals.pop(approval_id, None)
            await self._emit_status()

    async def reply_approval(self, approval_id: str, value: str) -> dict[str, Any]:
        future = self._pending_approvals.get(approval_id)
        if future is None or future.done():
            return {"accepted": False, "reason": "approval not pending"}
        decision = parse_approval_response(value)
        future.set_result(decision)
        return {"accepted": True, "decision": decision.value}

    async def reply_prompt(self, prompt_id: str, answer: str) -> dict[str, Any]:
        pending = self._pending_prompts.pop(prompt_id, None)
        if pending is None:
            return {"accepted": False, "reason": "prompt not pending"}
        normalized = self._normalize_prompt_answer(answer, pending.choices)
        if pending.kind == "resume":
            session_id = self._resolve_resume_arg(normalized, pending.records)
            if session_id is None:
                return {"accepted": False, "reason": "invalid session selection"}
            await self._resume_session(session_id)
            return {"accepted": True, "kind": "resume", "session_id": session_id}
        if pending.future is not None and not pending.future.done():
            pending.future.set_result(normalized)
            return {"accepted": True, "kind": pending.kind}
        await self.submit(normalized)
        return {"accepted": True, "kind": pending.kind}

    async def command(self, text: str) -> dict[str, Any]:
        command = parse_slash_command(text)
        if command is None:
            return {"handled": False, "reason": "not a slash command"}
        handled = await self.handle_command(command)
        await self._emit_status()
        return {"handled": handled, "exit": self.should_exit}

    async def interrupt_current_turn(self, *, reason: str = "interrupt") -> None:
        task = self._running_task
        if task is None or task.done():
            await self._emit_notice("no running turn")
            return
        await self._emit_notice(f"interrupting current turn: {reason}")
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        await self._emit_notice("turn interrupted")
        await self._emit_status()

    async def shutdown(self) -> None:
        self.should_exit = True
        if self._scheduler is not None:
            await self._scheduler.stop()
            self._scheduler = None
        task = self._running_task
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        for future in self._pending_approvals.values():
            if not future.done():
                future.set_result(ApprovalDecision("deny", "TUI shutdown"))
        for prompt in self._pending_prompts.values():
            if prompt.future is not None and not prompt.future.done():
                prompt.future.set_result("")
        if self._task_unsubscribe is not None:
            self._task_unsubscribe()
            self._task_unsubscribe = None
        await self.emit("channel.shutdown", {})

    def _start_scheduler(self) -> None:
        if self._scheduler is None:
            self._scheduler = start_scheduler_for_app(self.app)

    async def handle_command(self, command: SlashCommand) -> bool:
        handlers = {
            "help": self._help,
            "status": self._status,
            "core": self._core,
            "versions": self._versions,
            "provider": self._provider,
            "doctor": self._doctor,
            "tools": self._tools,
            "skills": self._skills,
            "skill": self._skill,
            "packages": self._packages,
            "tool-display": self._tool_display,
            "sessions": self._sessions,
            "subagents": self._subagents,
            "resume": self._resume,
            "new": self._new,
            "compact": self._compact,
            "last": self._last,
            "trace": self._trace,
            "events": self._events,
            "busy": self._busy,
            "interrupt": self._interrupt,
            "exit": self._exit,
            "quit": self._exit,
        }
        handler = handlers.get(command.name)
        if handler is None:
            await self._emit_notice(f"unknown command: /{command.name}", level="warning")
            return True
        return await handler(command.args)

    async def _run_message(self, text: str) -> None:
        await self._run_inbound(self._user_inbound(text))

    async def _run_inbound(self, inbound: InteractionInbound) -> None:
        try:
            if not _is_background_completion(inbound):
                await self.emit("interaction.message", {"role": "user", "text": inbound.text})
            result = await self.runtime.handle(inbound, bridge=self)
            await self.deliver(result)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._last_error = str(exc)
            await self.emit("interaction.error", {"message": str(exc), "source": "tui_bridge"})
        finally:
            self._running_task = None
            await self._emit_status()
            await self._drain_next_queued_input()

    async def _drain_next_queued_input(self) -> None:
        if self.running or self._queued_inputs.empty() or self.should_exit:
            return
        inbound = await self._next_queued_input()
        self._running_task = asyncio.create_task(self._run_inbound(inbound))
        await self._emit_status()

    async def _next_queued_input(self) -> InteractionInbound:
        pending: list[InteractionInbound] = []
        while not self._queued_inputs.empty():
            with contextlib.suppress(asyncio.QueueEmpty):
                pending.append(self._queued_inputs.get_nowait())
        user_index = next((index for index, item in enumerate(pending) if not _is_background_completion(item)), None)
        selected_index = user_index if user_index is not None else 0
        selected = pending.pop(selected_index)
        if not _is_background_completion(selected):
            completions = [item for item in pending if _is_background_completion(item)]
            pending = [item for item in pending if not _is_background_completion(item)]
            if completions:
                selected = _merge_completion_inbounds(selected, completions)
        for item in pending:
            self._queued_inputs.put_nowait(item)
        return selected

    def _merge_pending_completions_into(self, inbound: InteractionInbound) -> InteractionInbound:
        stored_completions = self._stored_completion_inbounds()
        if self._queued_inputs.empty():
            if not stored_completions:
                return inbound
            return _merge_completion_inbounds(inbound, stored_completions)
        pending: list[InteractionInbound] = []
        while not self._queued_inputs.empty():
            with contextlib.suppress(asyncio.QueueEmpty):
                pending.append(self._queued_inputs.get_nowait())
        completions = stored_completions + [item for item in pending if _is_background_completion(item)]
        for item in [item for item in pending if not _is_background_completion(item)]:
            self._queued_inputs.put_nowait(item)
        if not completions:
            return inbound
        return _merge_completion_inbounds(inbound, completions)

    def _stored_completion_inbounds(self) -> list[InteractionInbound]:
        completions: list[InteractionInbound] = []
        for event in self.app.task_worker.pending_events_for_session(self.app.runner.session_id):
            completions.append(
                _task_completion_inbound(
                    event,
                    channel="tui",
                    source="local",
                    reply_to=None,
                    conversation_key=self._conversation_key(),
                )
            )
            self.app.task_worker.clear_pending_event(event.event_id)
        return completions

    def _on_task_completion(self, event: RuntimeTaskCompletionEvent) -> None:
        if event.owner_session_id != self.app.runner.session_id or self.should_exit:
            return
        try:
            asyncio.get_running_loop().create_task(self._enqueue_task_completion(event))
        except RuntimeError:
            return

    async def _enqueue_task_completion(self, event: RuntimeTaskCompletionEvent) -> None:
        inbound = _task_completion_inbound(
            event,
            channel="tui",
            source="local",
            reply_to=None,
            conversation_key=self._conversation_key(),
        )
        self.app.task_worker.clear_pending_event(event.event_id)
        await self._emit_notice(f"background task {event.task_id} {event.status}: {shorten_text(event.summary, 100)}")
        if self.running or not self._queued_inputs.empty():
            await self._queued_inputs.put(inbound)
            await self._emit_status()
            return
        self._running_task = asyncio.create_task(self._run_inbound(inbound))
        await self._emit_status()

    async def _open_prompt(self, prompt: UserPromptRequest, *, kind: str, wait: bool) -> PendingPrompt:
        self._prompt_counter += 1
        prompt_id = f"prompt_{self._prompt_counter}"
        future = asyncio.get_running_loop().create_future() if wait else None
        pending = PendingPrompt(
            prompt_id=prompt_id,
            question=prompt.question,
            choices=list(prompt.choices),
            kind=kind,
            future=future,
            metadata=dict(prompt.metadata or {}),
        )
        self._pending_prompts[prompt_id] = pending
        await self.emit(
            "interaction.prompt.request",
            {
                "prompt_id": prompt_id,
                "kind": kind,
                "question": prompt.question,
                "choices": list(prompt.choices),
                "metadata": _json_safe(prompt.metadata),
            },
        )
        await self._emit_status()
        return pending

    async def _emit_ready(self) -> None:
        await self.emit(
            "interaction.ready",
            {
                "core_id": self.app.runner.core_id,
                "session_id": self.app.runner.session_id,
                "workspace": str(self.app.workspace.root),
                "provider": self.app.provider_name,
                "model": self.app.model_name,
                "runtime_timezone": self.app.runtime_timezone.name,
                "runtime_timezone_source": self.app.runtime_timezone.source,
                "tool_display": self.tool_display,
                "user_message_align": self.app.user_message_align,
                "demiurge_theme_color": self.app.demiurge_theme_color,
                "user_theme_color": self.app.user_theme_color,
                "busy_mode": self.busy_mode,
                "slash_commands": [_slash_spec_dict(spec) for spec in specs_for_surface("tui")],
            },
        )

    async def _emit_status(self) -> None:
        await self.emit("interaction.status", self._status_payload())

    async def _emit_history_snapshot(self, session_id: str) -> None:
        await self.emit(
            "interaction.history",
            {
                "session_id": session_id,
                "items": self._history_items(session_id),
            },
        )

    def _history_items(self, session_id: str) -> list[dict[str, Any]]:
        tool_events = _tool_history_events(EventLog(self.app.home, session_id).read_all())
        items: list[dict[str, Any]] = []
        for message in self.app.session_runtime.read_messages(session_id):
            if message.visible and message.role in {"user", "assistant", "system"}:
                if message.content:
                    items.append(
                        {
                            "id": f"history_message_{message.id}",
                            "type": "message",
                            "role": message.role,
                            "text": message.content,
                            "metadata": _json_safe(
                                {
                                    **(message.metadata or {}),
                                    "message_id": message.id,
                                    "turn_id": message.turn_id,
                                    "historical": True,
                                }
                            ),
                        }
                    )
                continue
            if message.role == "tool" and self.tool_display != "quiet":
                tool = _historical_tool_item(message, tool_events, full=self.tool_display == "full")
                if tool is not None:
                    items.append(tool)
        return items[-500:]

    def _status_payload(self) -> dict[str, Any]:
        pointer = self.app.version_store.active_pointer(self.app.runner.core_id)
        return {
            "workspace": str(self.app.workspace.root),
            "core_id": pointer.core_id,
            "core_version": pointer.active_version,
            "session_id": self.app.runner.session_id,
            "provider": self.app.provider_name,
            "model": self.app.model_name,
            "runtime_timezone": self.app.runtime_timezone.name,
            "runtime_timezone_source": self.app.runtime_timezone.source,
            "status": "running" if self.running else "idle",
            "tool_display": self.tool_display,
            "user_message_align": self.app.user_message_align,
            "demiurge_theme_color": self.app.demiurge_theme_color,
            "user_theme_color": self.app.user_theme_color,
            "busy_mode": self.busy_mode,
            "queued_inputs": self._queued_inputs.qsize(),
            "background_tasks": self.app.runner.background_task_count,
            "message_count": self.app.session_runtime.message_count(self.app.runner.session_id),
            "pending_prompts": len(self._pending_prompts),
            "pending_approvals": len(self._pending_approvals),
            "last_error": self._last_error,
        }

    async def _emit_notice(self, text: str, *, level: str = "info") -> None:
        await self.emit(
            "interaction.deliver",
            {
                "channel": "tui",
                "deliveries": [
                    {
                        "type": "text",
                        "kind": "notice",
                        "text": text,
                        "fallback_text": text,
                        "visible": True,
                        "metadata": {"level": level},
                    }
                ],
            },
        )

    async def _emit_tool_results(self, records: list[ToolExecutionRecord]) -> None:
        if self.tool_display == "quiet":
            return
        await self.emit(
            "interaction.deliver",
            {
                "channel": "tui",
                "deliveries": [],
                "tool_results": [_tool_record_dict(index, record, full=self.tool_display == "full") for index, record in enumerate(records, start=1)],
                "tool_display": self.tool_display,
            },
        )

    async def _help(self, _: str) -> bool:
        lines = ["# Commands", ""]
        for spec in SLASH_COMMANDS:
            usage = f" `{spec.usage}`" if spec.usage else ""
            lines.append(f"- `/{spec.name}` - {spec.description}{usage}")
        lines.append("")
        lines.append("Enter submits. Ctrl-C interrupts a running turn.")
        await self._emit_command_output("help", "\n".join(lines))
        return True

    async def _status(self, _: str) -> bool:
        status = self.app.status()
        status.update(
            {
                "busy_mode": self.busy_mode,
                "current_status": "running" if self.running else "idle",
                "queued_inputs": self._queued_inputs.qsize(),
                "tool_display": self.tool_display,
            }
        )
        await self._emit_command_output("status", _format_key_values("Status", status))
        return True

    async def _core(self, _: str) -> bool:
        pointer = self.app.version_store.active_pointer(self.app.runner.core_id)
        await self._emit_command_output("core", f"{pointer.core_id}@{pointer.active_version}")
        return True

    async def _versions(self, _: str) -> bool:
        pointer = self.app.version_store.active_pointer(self.app.runner.core_id)
        versions = self.app.version_store.list_versions(pointer.core_id)
        text = "\n".join(f"{'*' if version == pointer.active_version else ' '} {version}" for version in versions)
        await self._emit_command_output("versions", text)
        return True

    async def _provider(self, _: str) -> bool:
        await self._emit_command_output("provider", self.app.provider_name)
        return True

    async def _doctor(self, _: str) -> bool:
        report = DoctorRuntime(
            home=self.app.home,
            source_agents_root=self.app.source_agents_root,
            core_id=self.app.runner.core_id,
        ).run()
        rows = [(finding.severity, finding.code, finding.message, finding.remediation or "") for finding in report.findings]
        await self._emit_command_output("doctor", _format_table(["severity", "code", "message", "remediation"], rows, title="Doctor"))
        return True

    async def _tools(self, _: str) -> bool:
        core = self._active_core()
        rows = [
            (
                entry.name,
                entry.source,
                entry.risk,
                entry.capability or "",
                entry.approval_policy,
                f"{entry.model_output_policy}/{entry.display_policy}",
            )
            for entry in self.app.tool_runtime.registry_for(core)
        ]
        await self._emit_command_output("tools", _format_table(["name", "source", "risk", "capability", "approval", "output"], rows, title="Tools"))
        return True

    async def _skills(self, args: str) -> bool:
        category = args.strip() or None
        core = self._active_core()
        rows = [
            (skill.name, skill.category, skill.description, str(sum(len(files) for files in skill.linked_files.values())))
            for skill in core.skills
            if category is None or skill.category == category
        ]
        await self._emit_command_output("skills", _format_table(["name", "category", "description", "linked"], rows, title="Skills"))
        return True

    async def _skill(self, args: str) -> bool:
        parts = args.split(maxsplit=1)
        if not parts:
            await self._emit_command_output("skill", "usage: /skill <name> [file_path]")
            return True
        name = parts[0]
        file_path = parts[1] if len(parts) > 1 else None
        core = self._active_core()
        result = await self.app.tool_runtime.execute(
            ToolCall(
                name="skill_view",
                arguments={"name": name, **({"file_path": file_path} if file_path else {})},
                id="tui_skill_view",
            ),
            core=core,
            turn=self._tui_turn(core),
            capability=CapabilityFacade(core),
            emit_event=self.app.runner.event_log.emit,
        )
        content = result.content if result.is_error else str(result.data.get("content") if isinstance(result.data, dict) else result.content)
        await self._emit_command_output("skill", content)
        return True

    async def _packages(self, args: str) -> bool:
        host_config = load_host_config(self.app.host_config_path)[0]
        repositories = load_package_repository_collection(
            home=self.app.home,
            repository_configs=host_config.packages.repositories,
        )
        manager = PackageManager(version_store=self.app.version_store, repository=repositories)
        parts = args.split()
        core_id = self.app.runner.core_id
        if not parts:
            result = manager.list(core_id=core_id)
            installed_ids = {item.package_id for item in result.installed}
            rows = [
                ("*" if package.package_id in installed_ids else "", package.ref, ", ".join(package.tags), package.summary)
                for package in result.packages
            ]
            await self._emit_command_output("packages", _format_table(["", "package", "tags", "summary"], rows, title=f"Packages -> {core_id}"))
            return True
        action = parts[0]
        if action in {"install", "uninstall"}:
            if len(parts) != 2:
                await self._emit_command_output("packages", f"usage: /packages {action} <package>")
                return True
            try:
                result = manager.install(core_id=core_id, package_id=parts[1]) if action == "install" else manager.uninstall(core_id=core_id, package_id=parts[1])
            except PackageOperationError as exc:
                await self._emit_command_output("packages", f"package {action} failed: {exc}")
                return True
            await self._emit_command_output("packages", f"{result.action}ed {result.package_ref} for {result.core_id}")
            return True
        try:
            package = manager.repositories.resolve_package_ref(action)
        except PackageOperationError:
            package = None
        await self._emit_command_output("packages", f"unknown package: {action}" if package is None else _format_key_values(f"Package: {package.ref}", asdict(package)))
        return True

    async def _sessions(self, args: str) -> bool:
        limit = int(args.strip()) if args.strip().isdigit() else 20
        records = self.app.session_runtime.list_sessions(core_id=self.app.runner.core_id, limit=limit)
        await self._emit_command_output("sessions", _format_sessions(records, active_session_id=self.app.runner.session_id))
        return True

    async def _subagents(self, args: str) -> bool:
        text = await subagents_command_text(
            self.app.task_worker,
            session_id=self.app.runner.session_id,
            args=args,
        )
        await self._emit_command_output("subagents", text)
        return True

    async def _resume(self, args: str) -> bool:
        raw = args.strip()
        records = self.app.session_runtime.list_sessions(core_id=self.app.runner.core_id, limit=20)
        if not raw:
            self._prompt_counter += 1
            prompt_id = f"prompt_{self._prompt_counter}"
            pending = PendingPrompt(
                prompt_id=prompt_id,
                question="Resume session",
                choices=[record.session_id for record in records],
                kind="resume",
                records=records,
                metadata={"kind": "resume"},
            )
            self._pending_prompts[prompt_id] = pending
            await self.emit(
                "interaction.prompt.request",
                {
                    "prompt_id": prompt_id,
                    "kind": "resume",
                    "question": "Resume session",
                    "choices": [record.session_id for record in records],
                    "records": [_session_record_dict(record) for record in records],
                    "metadata": {"kind": "resume"},
                },
            )
            await self._emit_status()
            return True
        session_id = self._resolve_resume_arg(raw, records)
        if session_id is not None:
            await self._resume_session(session_id)
        return True

    async def _resume_session(self, session_id: str) -> None:
        try:
            self.app.runner.resume_session(session_id)
        except FileNotFoundError as exc:
            await self._emit_notice(str(exc), level="error")
            return
        self.runtime = InteractionRuntime(self.app.runner)
        await self._emit_history_snapshot(session_id)
        await self._emit_notice(f"resumed session: {session_id}")
        await self._emit_status()

    async def _new(self, _: str) -> bool:
        session_id = self.app.runner.start_new_session(channel="tui", source="local")
        self.runtime = InteractionRuntime(self.app.runner)
        await self._emit_history_snapshot(session_id)
        await self._emit_notice(f"new session: {session_id}")
        await self._emit_status()
        return True

    async def _compact(self, args: str) -> bool:
        result = await self.app.runner.compact_session(focus=args.strip() or None)
        if result.error:
            text = f"compact failed: {result.error}"
        elif result.skipped:
            text = result.summary
        else:
            text = f"compacted {result.compacted_count} message(s); summary message: {result.summary_message_id}"
        await self._emit_command_output("compact", text)
        return True

    async def _last(self, _: str) -> bool:
        return await self._trace("last")

    async def _trace(self, args: str) -> bool:
        turn_id = args.strip() or "last"
        if turn_id == "last":
            if self.app.runner.display_turns:
                turn_id = str(self.app.runner.display_turns[-1]["turn_id"])
            else:
                latest = self.app.session_runtime.latest_turn_id(self.app.runner.session_id)
                if latest:
                    turn_id = latest
                else:
                    await self._emit_command_output("trace", "no turns yet")
                    return True
        events = self.app.runner.event_log.for_turn(turn_id)
        if not events:
            latest = self.app.session_runtime.latest_turn_id(self.app.runner.session_id)
            if latest and latest != turn_id:
                events = self.app.runner.event_log.for_turn(latest)
                turn_id = latest
        if not events:
            await self._emit_command_output("trace", "no turns yet")
            return True
        rows = [(str(event.get("created_at", "")), str(event.get("type", "")), self._event_detail(event)) for event in events]
        await self._emit_command_output("trace", _format_table(["time", "type", "detail"], rows, title=f"Trace {turn_id}"))
        return True

    async def _events(self, args: str) -> bool:
        event_type: str | None = None
        limit = 10
        parts = args.split()
        if parts:
            if parts[0].isdigit():
                limit = int(parts[0])
            else:
                event_type = parts[0]
                if len(parts) > 1 and parts[1].isdigit():
                    limit = int(parts[1])
        rows = [
            (str(event.get("created_at", "")), str(event.get("type", "")), str(event.get("turn_id", "")), self._event_detail(event))
            for event in self.app.runner.event_log.tail(limit, event_type=event_type)
        ]
        await self._emit_command_output("events", _format_table(["time", "type", "turn", "detail"], rows, title="Events"))
        return True

    async def _tool_display(self, args: str) -> bool:
        if not args:
            await self._emit_command_output("tool-display", f"tool display: {self.tool_display}")
            return True
        level = parse_tool_display_level(args)
        if level is None:
            await self._emit_command_output("tool-display", "usage: /tool-display quiet|summary|full")
            return True
        self.tool_display = level
        await self._emit_command_output("tool-display", f"tool display: {self.tool_display}")
        return True

    async def _busy(self, args: str) -> bool:
        mode = args.strip().lower()
        if not mode:
            await self._emit_command_output("busy", f"busy mode: {self.busy_mode}")
            return True
        if mode not in {"interrupt", "queue"}:
            await self._emit_command_output("busy", "usage: /busy interrupt|queue")
            return True
        self.busy_mode = mode
        await self._emit_command_output("busy", f"busy mode: {self.busy_mode}")
        return True

    async def _interrupt(self, _: str) -> bool:
        await self.interrupt_current_turn(reason="/interrupt")
        return True

    async def _exit(self, _: str) -> bool:
        await self.shutdown()
        return False

    async def _emit_command_output(self, command: str, text: str) -> None:
        await self.emit(
            "interaction.deliver",
            {
                "channel": "tui",
                "deliveries": [
                    {
                        "type": "text",
                        "kind": "message",
                        "text": text,
                        "fallback_text": text,
                        "visible": True,
                        "metadata": {"command": command, "role": "system"},
                    }
                ],
            },
        )

    def _conversation_key(self) -> str:
        return f"tui:{self.app.runner.session_id}"

    def _user_inbound(self, text: str) -> InteractionInbound:
        return InteractionInbound(
            channel="tui",
            text=text,
            source="local",
            reply_to=None,
            conversation_key=self._conversation_key(),
        )

    def _normalize_prompt_answer(self, answer: str, choices: list[str]) -> str:
        text = str(answer or "").strip()
        if text.isdigit():
            index = int(text) - 1
            if 0 <= index < len(choices):
                return choices[index]
        return text or (choices[0] if choices else "")

    def _resolve_resume_arg(self, raw: str, records: list[SessionRecord]) -> str | None:
        value = _strip_outer_wrappers(raw.strip())
        if value.isdigit():
            index = int(value) - 1
            if 0 <= index < len(records):
                return records[index].session_id
            return None
        return value

    def _active_core(self):
        return self.app.core_loader.load(self.app.version_store.active_core_path(self.app.runner.core_id))

    def _tui_turn(self, core):
        return TurnContext(
            session_id=self.app.runner.session_id,
            turn_id="tui_command",
            core_id=core.core_id,
            core_version=core.version,
            user_input=AgentInput(content=""),
            state={},
            metadata={"channel": "tui", "source": "local", "target": "local"},
        )

    def _event_detail(self, event: dict[str, Any]) -> str:
        event_type = event.get("type")
        if event_type == "actions.requested":
            actions = event.get("actions") or []
            return ", ".join(str(action.get("name")) for action in actions if isinstance(action, dict))
        if event_type == "action.result":
            status = "error" if event.get("is_error") else "ok"
            return f"{event.get('tool_name')} {status}: {shorten_text(str(event.get('content') or ''))}"
        if event_type and str(event_type).startswith("approval."):
            return " ".join(
                str(part)
                for part in [event.get("tool_name"), event.get("decision"), event.get("reason"), event.get("summary")]
                if part
            )
        if event_type == "message.completed":
            return shorten_text(str(event.get("content") or ""))
        return shorten_text(json.dumps({k: v for k, v in event.items() if k not in {"id", "created_at", "type", "session_id"}}, ensure_ascii=False))


def _delivery_dict(delivery: Any) -> dict[str, Any]:
    return {
        "type": delivery.type,
        "kind": delivery.kind,
        "text": delivery.text,
        "blocks": _json_safe(delivery.blocks),
        "fallback_text": delivery.fallback_text,
        "payload": _json_safe(delivery.payload),
        "artifacts": _json_safe(delivery.artifacts),
        "visible": delivery.visible,
        "history_policy": delivery.history_policy,
        "metadata": _json_safe(delivery.metadata),
    }


def _tool_history_events(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for event in events:
        if event.get("type") == "actions.requested":
            for action in event.get("actions") or []:
                if not isinstance(action, dict):
                    continue
                call_id = str(action.get("id") or "")
                if not call_id:
                    continue
                by_id.setdefault(call_id, {}).update(
                    {
                        "id": call_id,
                        "name": str(action.get("name") or ""),
                        "arguments": action.get("arguments") if isinstance(action.get("arguments"), dict) else {},
                    }
                )
            continue
        if event.get("type") != "action.result":
            continue
        call_id = str(event.get("tool_call_id") or "")
        if not call_id:
            continue
        by_id.setdefault(call_id, {}).update(
            {
                "id": call_id,
                "name": str(event.get("tool_name") or ""),
                "content": str(event.get("content") or ""),
                "display_output": str(event.get("display_output") or ""),
                "model_output": event.get("model_output"),
                "is_error": bool(event.get("is_error")),
                "data": event.get("data"),
            }
        )
    return by_id


def _historical_tool_item(message: Any, events: dict[str, dict[str, Any]], *, full: bool) -> dict[str, Any] | None:
    metadata = message.metadata or {}
    call_id = str(metadata.get("tool_call_id") or "")
    event = events.get(call_id, {}) if call_id else {}
    name = str(event.get("name") or metadata.get("tool_name") or "")
    if not name and not message.content:
        return None
    result_text = str(event.get("display_output") or event.get("content") or message.content or "")
    tool = {
        "index": 1,
        "name": name or "tool",
        "id": call_id or message.id,
        "status": "error" if bool(event.get("is_error") or metadata.get("is_error")) else "ok",
        "summary": shorten_text(result_text),
    }
    if full:
        tool.update(
            {
                "arguments": _json_safe(event.get("arguments") if isinstance(event.get("arguments"), dict) else {}),
                "result": result_text,
                "model_output": event.get("model_output"),
            }
        )
    return {
        "id": f"history_tool_{call_id or message.id}",
        "type": "tool",
        "display": "full" if full else "summary",
        "tools": [tool],
    }


def _task_completion_inbound(
    event: RuntimeTaskCompletionEvent,
    *,
    channel: str,
    source: str,
    reply_to: str | None,
    conversation_key: str | None,
) -> InteractionInbound:
    return InteractionInbound(
        channel=channel,
        text=event.to_inbound_text(),
        source=source,
        reply_to=reply_to,
        conversation_key=conversation_key,
        metadata=event.to_metadata(),
    )


def _is_background_completion(inbound: InteractionInbound) -> bool:
    return inbound.metadata.get("trigger") == "background_task"


def _merge_completion_inbounds(user_inbound: InteractionInbound, completions: list[InteractionInbound]) -> InteractionInbound:
    metadata = dict(user_inbound.metadata)
    metadata["merged_background_tasks"] = [
        item.metadata.get("task_id") for item in completions if item.metadata.get("task_id")
    ]
    completion_text = "\n\n".join(item.text for item in completions if item.text)
    text = "\n\n".join(
        part
        for part in [
            user_inbound.text,
            "[SYSTEM: Pending background task events merged into this user turn]",
            completion_text,
        ]
        if part
    )
    return InteractionInbound(
        channel=user_inbound.channel,
        text=text,
        source=user_inbound.source,
        reply_to=user_inbound.reply_to,
        conversation_key=user_inbound.conversation_key,
        metadata=metadata,
        attachments=list(user_inbound.attachments),
    )


def _tool_record_dict(index: int, record: ToolExecutionRecord, *, full: bool) -> dict[str, Any]:
    result_text = record.result.display_output or record.result.content or ""
    item = {
        "index": index,
        "name": record.call.name,
        "id": record.call.id,
        "status": "error" if record.result.is_error else "ok",
        "summary": shorten_text(result_text),
    }
    if full:
        item.update(
            {
                "arguments": _json_safe(record.call.arguments),
                "result": result_text,
                "model_output": record.result.model_output,
            }
        )
    return item


def _session_record_dict(record: SessionRecord) -> dict[str, Any]:
    return {
        "session_id": record.session_id,
        "title": record.title,
        "updated_at": record.updated_at,
        "channel": record.channel,
        "message_count": record.message_count,
        "preview": record.preview,
    }


def _slash_spec_dict(spec: SlashCommandSpec) -> dict[str, Any]:
    return {
        "name": spec.name,
        "description": spec.description,
        "group": spec.group,
        "usage": spec.usage,
    }


def _format_sessions(records: list[SessionRecord], *, active_session_id: str) -> str:
    rows = [
        (
            str(index),
            "*" if record.session_id == active_session_id else "",
            record.session_id,
            record.updated_at,
            record.channel or "",
            str(record.message_count),
            record.preview or "",
        )
        for index, record in enumerate(records, start=1)
    ]
    return _format_table(["#", "", "session_id", "updated", "channel", "messages", "preview"], rows, title="Sessions")


def _format_key_values(title: str, values: dict[str, Any]) -> str:
    rows = [(str(key), json.dumps(_json_safe(value), ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)) for key, value in values.items()]
    return _format_table(["key", "value"], rows, title=title)


def _format_table(headers: list[str], rows: list[tuple[Any, ...]], *, title: str | None = None) -> str:
    table_rows = [[str(cell) for cell in row] for row in rows]
    widths = [len(header) for header in headers]
    for row in table_rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], min(len(cell), 72))
    lines = [f"## {title}", ""] if title else []
    lines.append(" | ".join(header.ljust(widths[index]) for index, header in enumerate(headers)))
    lines.append(" | ".join("-" * width for width in widths))
    for row in table_rows:
        lines.append(" | ".join(shorten_text(cell, limit=widths[index]).ljust(widths[index]) for index, cell in enumerate(row)))
    return "\n".join(lines)


def _json_safe(value: Any) -> Any:
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def shorten_text(text: str, limit: int = 160) -> str:
    normalized = " ".join(str(text).split())
    if len(normalized) <= limit:
        return normalized
    if limit <= 15:
        return normalized[:limit]
    return f"{normalized[: limit - 15]}...[truncated]"


def _strip_outer_wrappers(value: str) -> str:
    pairs = {"<": ">", "[": "]", '"': '"', "'": "'"}
    if len(value) >= 2 and value[0] in pairs and value[-1] == pairs[value[0]]:
        return value[1:-1]
    return value
