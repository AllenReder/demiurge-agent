from __future__ import annotations

import asyncio
import contextlib
import json
from dataclasses import asdict, dataclass, field, replace
from typing import Any

from demiurge.app import DemiurgeApp, load_host_config
from demiurge.diagnostics.doctor import DoctorRuntime
from demiurge.runtime.tasks import RuntimeTaskCompletionEvent
from demiurge.packages import PackageManager, PackageOperationError, load_package_repository_collection
from demiurge.providers import ToolCall
from demiurge.runtime.completions import is_background_completion
from demiurge.runtime.delegation import subagents_command_text
from demiurge.runtime.event_commands import build_events_command_text, build_trace_command_text
from demiurge.runtime.history_display import build_history_items
from demiurge.runtime.interactions import (
    InteractionInbound,
    InteractionOutbound,
    InteractionRuntime,
    SessionRouteBinding,
    ToolInteractionRecord,
    UserPromptRequest,
)
from demiurge.runtime.ingress import ConversationIngressState, ConversationTurnController
from demiurge.runtime.approvals import parse_approval_response
from demiurge.runtime.outbound_delivery import ui_delivery_steps
from demiurge.runtime.prompts import normalize_prompt_answer
from demiurge.runtime.session_commands import (
    build_session_list_view,
    format_sessions_table,
    resolve_session_choice,
    resume_bound_session,
    session_list_view,
    start_bound_session,
)
from demiurge.runtime.status_commands import RuntimeStatusView, build_runtime_status_view, runtime_status_key_values
from demiurge.runtime.text_format import format_key_values, format_table, json_safe, shorten_text
from demiurge.runtime.tool_display import tool_call_item
from demiurge.sdk import AgentInput, TurnContext
from demiurge.scheduler import SchedulerService, start_scheduler_for_app
from demiurge.security.approval import ApprovalDecision, ApprovalRequest
from demiurge.security.capabilities import CapabilityFacade
from demiurge.slash import SlashCommand, SlashCommandSpec, help_text_for_surface, parse_slash_command, specs_for_surface
from demiurge.storage import EventLog, SessionRecord
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
    InteractionInbound/InteractionOutbound and implements SessionInteractionRoute.
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
        self._route_binding = SessionRouteBinding(route=self)
        self.tool_display = parse_tool_display_level(tool_display or app.tool_display) or "summary"
        resolved_busy_mode = busy_mode or app.channel_busy_mode
        self.busy_mode = resolved_busy_mode if resolved_busy_mode in {"interrupt", "queue"} else "interrupt"
        self._ingress_state = ConversationIngressState(
            runtime=self.runtime,
            busy_mode=self.busy_mode,
            route_binding=self._route_binding,
            conversation_key=self._conversation_key(),
            source="local",
        )
        self._queued_inputs = self._ingress_state.queue
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
        return ConversationTurnController(self._ingress_state).running

    async def initialize(self) -> dict[str, Any]:
        self._start_scheduler()
        self._bind_current_session()
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
        self._ingress_state.remember_route(inbound)
        if self.running:
            task = self._ingress_state.active_task

            async def notify(decision) -> None:
                if decision.kind == "queue":
                    await self._emit_notice(f"queued input: {shorten_text(text, 100)}")
                    return
                if decision.kind == "interrupt":
                    await self._emit_notice("interrupting current turn: new input")

            decision = await ConversationTurnController(self._ingress_state).handle_busy_inbound(inbound, notify=notify)
            if decision.cancel_active and task is not None:
                with contextlib.suppress(asyncio.CancelledError):
                    await task
                await self._emit_notice("turn interrupted")
            await self._emit_status()
            return {"accepted": True, "queued": True}
        inbound = self._merge_pending_completions_into(inbound)
        ConversationTurnController(self._ingress_state).start(inbound, self._run_inbound)
        await self._emit_status()
        return {"accepted": True, "queued": False}

    async def wait_for_idle(self) -> None:
        task = self._ingress_state.active_task
        if task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def deliver(self, outbound: InteractionOutbound) -> None:
        if outbound.session_id != self.app.runner.session_id:
            raise RuntimeError(
                f"TUI route bound to session `{self.app.runner.session_id}` received outbound for `{outbound.session_id}`"
            )
        try:
            for step in ui_delivery_steps(outbound):
                if step.kind == "tool_calls":
                    await self._emit_tool_calls(list(step.tool_calls), outbound=outbound)
                    continue
                if step.kind == "deliveries":
                    await self._emit_deliveries(outbound, list(step.deliveries))
                    continue
                if step.kind == "prompt" and step.prompt is not None:
                    await self._open_prompt(step.prompt, kind="clarify", wait=False)
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
                "metadata": json_safe(outbound.metadata),
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
        decision = parse_approval_response(value, actor="TUI user")
        future.set_result(decision)
        return {"accepted": True, "decision": decision.value}

    async def reply_prompt(self, prompt_id: str, answer: str) -> dict[str, Any]:
        pending = self._pending_prompts.pop(prompt_id, None)
        if pending is None:
            return {"accepted": False, "reason": "prompt not pending"}
        normalized = normalize_prompt_answer(answer, pending.choices, empty="first").text
        if pending.kind == "resume":
            view = session_list_view(pending.records, active_session_id=self.app.runner.session_id)
            resolution = resolve_session_choice(normalized, view)
            if not resolution.ok:
                return {"accepted": False, "reason": "invalid session selection"}
            assert resolution.session_id is not None
            await self._resume_session(resolution.session_id)
            return {"accepted": True, "kind": "resume", "session_id": resolution.session_id}
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
        if not self.running:
            await self._emit_notice("no running turn")
            return
        await ConversationTurnController(self._ingress_state).cancel_active(
            before_cancel=lambda: self._emit_notice(f"interrupting current turn: {reason}")
        )
        await self._emit_notice("turn interrupted")
        await self._emit_status()

    async def shutdown(self) -> None:
        self.should_exit = True
        if self._scheduler is not None:
            await self._scheduler.stop()
            self._scheduler = None
        task = self._ingress_state.active_task
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
        self._route_binding.unbind(self.app.runner.interaction_router)
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
            "evolve": self._evolve,
            "rollback": self._rollback,
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
            if not is_background_completion(inbound):
                await self.emit("interaction.message", {"role": "user", "text": inbound.text})
            result = await self.runtime.handle(inbound, route_binding=self._route_binding)
            await self.deliver(result)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._last_error = str(exc)
            await self.emit("interaction.error", {"message": str(exc), "source": "tui_bridge"})
        finally:
            ConversationTurnController(self._ingress_state).finish(asyncio.current_task())
            await self._emit_status()
            await self._drain_next_queued_input()

    async def _drain_next_queued_input(self) -> None:
        if self.should_exit:
            return
        if await ConversationTurnController(self._ingress_state).drain_next(self._run_inbound):
            await self._emit_status()

    def _next_queued_input(self) -> InteractionInbound:
        return ConversationTurnController(self._ingress_state).next_queued_input()

    def _merge_pending_completions_into(self, inbound: InteractionInbound) -> InteractionInbound:
        return ConversationTurnController(self._ingress_state).merge_pending_completions(
            inbound,
            channel="tui",
            owner_id="bridge:tui:stored",
            fallback_source="local",
        )

    def _on_task_completion(self, event: RuntimeTaskCompletionEvent) -> None:
        if event.owner_session_id != self.app.runner.session_id or self.should_exit:
            return
        try:
            asyncio.get_running_loop().create_task(self._enqueue_task_completion(event))
        except RuntimeError:
            return

    async def _enqueue_task_completion(self, event: RuntimeTaskCompletionEvent) -> None:
        async def before_enqueue(_inbound: InteractionInbound) -> None:
            await self._emit_notice(f"background task {event.task_id} {event.status}: {shorten_text(event.summary, 100)}")

        result = await ConversationTurnController(self._ingress_state).enqueue_completion_event(
            event,
            channel="tui",
            owner_id="bridge:tui:enqueue",
            run=self._run_inbound,
            before_enqueue=before_enqueue,
        )
        if result.inbound is None:
            return
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
                "metadata": json_safe(prompt.metadata),
            },
        )
        await self._emit_status()
        return pending

    async def _emit_ready(self) -> None:
        pointer = self.app.version_store.active_pointer(self.app.runner.core_id)
        await self.emit(
            "interaction.ready",
            {
                "core_id": pointer.core_id,
                "core_revision": pointer.active_revision,
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
        return build_history_items(
            self.app.session_runtime.read_messages(session_id),
            EventLog(self.app.home, session_id).read_all(),
            tool_display=self.tool_display,
        )

    def _status_payload(self) -> dict[str, Any]:
        pointer = self.app.version_store.active_pointer(self.app.runner.core_id)
        view = self._runtime_status_view()
        return {
            "workspace": str(self.app.workspace.root),
            "core_id": pointer.core_id,
            "core_revision": pointer.active_revision,
            "session_id": view.session_id,
            "provider": self.app.provider_name,
            "model": self.app.model_name,
            "runtime_timezone": self.app.runtime_timezone.name,
            "runtime_timezone_source": self.app.runtime_timezone.source,
            "status": view.status_text,
            "tool_display": self.tool_display,
            "user_message_align": self.app.user_message_align,
            "demiurge_theme_color": self.app.demiurge_theme_color,
            "user_theme_color": self.app.user_theme_color,
            "busy_mode": view.busy_mode,
            "queued_inputs": view.queued_inputs,
            "background_tasks": self.app.runner.background_task_count,
            "message_count": view.message_count or 0,
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

    async def _emit_tool_calls(
        self,
        records: list[ToolInteractionRecord],
        *,
        outbound: InteractionOutbound | None = None,
    ) -> None:
        if self.tool_display == "quiet":
            return
        await self.emit(
            "interaction.deliver",
            {
                "channel": "tui",
                "session_id": outbound.session_id if outbound is not None else self.app.runner.session_id,
                "turn_id": outbound.turn_id if outbound is not None else None,
                "deliveries": [],
                "tool_calls": [tool_call_item(index, record, full=self.tool_display == "full") for index, record in enumerate(records, start=1)],
                "tool_display": self.tool_display,
            },
        )

    async def _help(self, _: str) -> bool:
        await self._emit_command_output(
            "help",
            help_text_for_surface("tui", footer_lines=("Enter submits. Ctrl-C interrupts a running turn.",)),
        )
        return True

    async def _status(self, _: str) -> bool:
        status = self.app.status()
        status.update(
            runtime_status_key_values(
                self._runtime_status_view(),
                extra=(("tool_display", self.tool_display),),
            )
        )
        await self._emit_command_output("status", format_key_values("Status", status))
        return True

    async def _core(self, _: str) -> bool:
        pointer = self.app.version_store.active_pointer(self.app.runner.core_id)
        await self._emit_command_output("core", f"{pointer.core_id}@{pointer.active_revision}")
        return True

    async def _versions(self, _: str) -> bool:
        pointer = self.app.version_store.active_pointer(self.app.runner.core_id)
        versions = self.app.version_store.list_versions(pointer.core_id)
        text = "\n".join(f"{'*' if version == pointer.active_revision else ' '} {version}" for version in versions)
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
        await self._emit_command_output("doctor", format_table(["severity", "code", "message", "remediation"], rows, title="Doctor"))
        return True

    async def _tools(self, _: str) -> bool:
        core = await self._active_core()
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
        await self._emit_command_output("tools", format_table(["name", "source", "risk", "capability", "approval", "output"], rows, title="Tools"))
        return True

    async def _skills(self, args: str) -> bool:
        category = args.strip() or None
        core = await self._active_core()
        rows = [
            (skill.name, skill.category, skill.description, str(sum(len(files) for files in skill.linked_files.values())))
            for skill in core.skills
            if category is None or skill.category == category
        ]
        await self._emit_command_output("skills", format_table(["name", "category", "description", "linked"], rows, title="Skills"))
        return True

    async def _skill(self, args: str) -> bool:
        parts = args.split(maxsplit=1)
        if not parts:
            await self._emit_command_output("skill", "usage: /skill <name> [file_path]")
            return True
        name = parts[0]
        file_path = parts[1] if len(parts) > 1 else None
        core = await self._active_core()
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
        manager = PackageManager(agents_root=self.app.version_store.agents_root, repository=repositories)
        parts = args.split()
        core_id = self.app.runner.core_id
        if not parts:
            result = manager.list(core_id=core_id)
            installed_by_id = {item.package_id: item for item in result.installed}
            rows = [
                (
                    "!" if installed_by_id.get(package.package_id) and installed_by_id[package.package_id].drift else "*" if package.package_id in installed_by_id else "",
                    package.ref,
                    ", ".join(package.tags),
                    package.summary,
                )
                for package in result.packages
            ]
            await self._emit_command_output("packages", format_table(["", "package", "tags", "summary"], rows, title=f"Packages -> {core_id}"))
            return True
        action = parts[0]
        if action in {"install", "uninstall"}:
            if len(parts) not in {2, 3}:
                await self._emit_command_output("packages", f"usage: /packages {action} <package> [--force-drift]")
                return True
            try:
                if action == "install":
                    result = await self._commit_package_transaction(
                        f"install {parts[1]}",
                        lambda: manager.install(core_id=core_id, package_id=parts[1]),
                    )
                else:
                    force_drift = len(parts) == 3 and parts[2] == "--force-drift"
                    result = await self._commit_package_transaction(
                        f"uninstall {parts[1]}",
                        lambda: manager.uninstall(core_id=core_id, package_id=parts[1], destructive=force_drift),
                    )
            except PackageOperationError as exc:
                await self._emit_command_output("packages", f"package {action} failed: {exc}")
                return True
            revision = f" @ {result.revision[:12]}" if result.revision else ""
            await self._emit_command_output("packages", f"{result.action}ed {result.package_ref} for {result.core_id}{revision}")
            return True
        try:
            package = manager.repositories.resolve_package_ref(action)
        except PackageOperationError:
            package = None
        await self._emit_command_output("packages", f"unknown package: {action}" if package is None else format_key_values(f"Package: {package.ref}", asdict(package)))
        return True

    async def _evolve(self, args: str) -> bool:
        parts = args.split(maxsplit=1)
        if not parts:
            await self._emit_command_output("evolve", "usage: /evolve <goal>|review <run_id>|promote <run_id>|discard <run_id>")
            return True
        action = parts[0]
        if action in {"review", "promote", "discard"}:
            run_id = parts[1].strip() if len(parts) > 1 else ""
            if not run_id:
                await self._emit_command_output("evolve", f"usage: /evolve {action} <run_id>")
                return True
            if action == "review":
                result = await self.app.evolution_runtime.review(run_id, target_core_id=self.app.runner.core_id)
                await self._emit_command_output(
                    "evolve",
                    f"review {run_id}: {'passed' if result.passed else 'failed'}\n"
                    f"proposal: {result.proposal_revision or '(none)'}\n"
                    f"report: {result.report_path}",
                )
                return True
            if action == "promote":
                result = await self.app.evolution_runtime.promote(run_id, target_core_id=self.app.runner.core_id, reason="tui promote")
                await self._emit_command_output("evolve", f"{result.summary}\nreport: {result.report_path}")
                return True
            payload = self.app.evolution_runtime.discard(run_id)
            await self._emit_command_output("evolve", json.dumps(payload, ensure_ascii=False, indent=2))
            return True
        result = await self.app.evolution_runtime.start(
            target_core_id=self.app.runner.core_id,
            goal=args,
            source_turn_id=None,
        )
        await self._emit_command_output(
            "evolve",
            f"{result.summary}\nrun_id: {result.run_id}\nagents_root: {result.agents_root}\nreport: {result.report_path}",
        )
        return True

    async def _rollback(self, args: str) -> bool:
        target = args.strip() or "previous"
        try:
            pointer = self.app.version_store.rollback(self.app.runner.core_id, target=target, reason="tui rollback")
        except Exception as exc:
            await self._emit_command_output("rollback", f"rollback failed: {exc}")
            return True
        await self._emit_command_output(
            "rollback",
            f"rollback committed: {pointer.active_revision[:12]}\nprevious: {pointer.previous_revision[:12] if pointer.previous_revision else '(none)'}",
        )
        return True

    async def _commit_package_transaction(self, action: str, operation):
        repository = self.app.version_store.core_repository
        await repository.prepare_live_for_edit_async(
            validate=lambda agents_root, changed_paths: self.app.gate_runner.run(agents_root, changed_paths=changed_paths)
        )
        with repository.live_transaction(reason=f"package {action}"):
            result = operation()
            changed_paths = repository.live_changed_paths()
            gates = await self.app.gate_runner.run(repository.active_agents_root(), changed_paths=changed_paths)
            if not gates.passed:
                failures = [phase for phase in gates.phases if not phase.passed]
                summary = "; ".join(f"{phase.name}: {phase.detail}" for phase in failures[:5]) or "unknown gate failure"
                raise PackageOperationError("package gates failed: " + summary)
            commit = repository.commit_live(reason=f"package {action}", summary=f"package {action}")
            return replace(result, revision=commit.revision, previous_revision=commit.previous_revision)

    async def _sessions(self, args: str) -> bool:
        limit = int(args.strip()) if args.strip().isdigit() else 20
        view = build_session_list_view(
            self.app.session_runtime,
            core_id=self.app.runner.core_id,
            active_session_id=self.app.runner.session_id,
            limit=limit,
        )
        await self._emit_command_output("sessions", format_sessions_table(view))
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
        view = build_session_list_view(
            self.app.session_runtime,
            core_id=self.app.runner.core_id,
            active_session_id=self.app.runner.session_id,
            limit=20,
        )
        if not raw:
            self._prompt_counter += 1
            prompt_id = f"prompt_{self._prompt_counter}"
            pending = PendingPrompt(
                prompt_id=prompt_id,
                question="Resume session",
                choices=view.session_ids,
                kind="resume",
                records=view.records,
                metadata={"kind": "resume"},
            )
            self._pending_prompts[prompt_id] = pending
            await self.emit(
                "interaction.prompt.request",
                {
                    "prompt_id": prompt_id,
                    "kind": "resume",
                    "question": "Resume session",
                    "choices": view.session_ids,
                    "records": [choice.as_dict() for choice in view.choices],
                    "metadata": {"kind": "resume"},
                },
            )
            await self._emit_status()
            return True
        resolution = resolve_session_choice(raw, view)
        if not resolution.ok:
            await self._emit_notice(resolution.message or "invalid session selection", level="error")
            return True
        assert resolution.session_id is not None
        await self._resume_session(resolution.session_id)
        return True

    async def _resume_session(self, session_id: str) -> None:
        result = resume_bound_session(self.app.runner, self._route_binding, session_id)
        if not result.ok:
            await self._emit_notice(result.message, level="error")
            return
        self.runtime = InteractionRuntime(self.app.runner)
        self._sync_ingress_state()
        await self._emit_history_snapshot(session_id)
        await self._emit_notice(f"resumed session: {session_id}")
        await self._emit_status()

    async def _new(self, _: str) -> bool:
        result = await start_bound_session(self.app.runner, self._route_binding, channel="tui", source="local")
        if not result.ok:
            await self._emit_notice(result.message, level="error")
            return True
        assert result.session_id is not None
        self.runtime = InteractionRuntime(self.app.runner)
        self._sync_ingress_state()
        await self._emit_history_snapshot(result.session_id)
        await self._emit_notice(f"new session: {result.session_id}")
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
        text = build_trace_command_text(
            self.app.runner.event_log,
            self.app.session_runtime,
            session_id=self.app.runner.session_id,
            display_turns=self.app.runner.display_turns,
            args=args,
        )
        await self._emit_command_output("trace", text)
        return True

    async def _events(self, args: str) -> bool:
        await self._emit_command_output("events", build_events_command_text(self.app.runner.event_log, args=args))
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
        self._ingress_state.busy_mode = mode
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

    def _sync_ingress_state(self) -> None:
        self._ingress_state.runtime = self.runtime
        self._ingress_state.busy_mode = self.busy_mode
        self._ingress_state.conversation_key = self._conversation_key()
        self._ingress_state.source = "local"
        self._ingress_state.reply_to = None
        self._ingress_state.metadata = {}

    def _user_inbound(self, text: str) -> InteractionInbound:
        return InteractionInbound(
            channel="tui",
            text=text,
            source="local",
            reply_to=None,
            conversation_key=self._conversation_key(),
        )

    def _bind_current_session(self) -> None:
        self._route_binding.bind(self.app.runner.interaction_router, self.app.runner.session_id)

    def _runtime_status_view(self) -> RuntimeStatusView:
        return build_runtime_status_view(
            self.app.runner,
            self.app.session_runtime,
            running=self.running,
            busy_mode=self.busy_mode,
            queued_inputs=self._queued_inputs.qsize(),
        )

    async def _active_core(self):
        return await self.app.load_active_core()

    def _tui_turn(self, core):
        return TurnContext(
            session_id=self.app.runner.session_id,
            turn_id="tui_command",
            core_id=core.core_id,
            core_revision=self.app.version_store.active_pointer(core.core_id).active_revision,
            user_input=AgentInput(content=""),
            metadata={"channel": "tui", "source": "local", "target": "local"},
        )

def _delivery_dict(delivery: Any) -> dict[str, Any]:
    return {
        "type": delivery.type,
        "kind": delivery.kind,
        "text": delivery.text,
        "blocks": json_safe(delivery.blocks),
        "fallback_text": delivery.fallback_text,
        "payload": json_safe(delivery.payload),
        "artifacts": json_safe(delivery.artifacts),
        "visible": delivery.visible,
        "history_policy": delivery.history_policy,
        "metadata": json_safe(delivery.metadata),
    }


def _slash_spec_dict(spec: SlashCommandSpec) -> dict[str, Any]:
    return {
        "name": spec.name,
        "description": spec.description,
        "group": spec.group,
        "usage": spec.usage,
    }
