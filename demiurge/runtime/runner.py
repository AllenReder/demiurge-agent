from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping

from demiurge.runtime.tasks import (
    RuntimeTaskKindError,
    RuntimeTaskWorker,
)
from demiurge.security.capabilities import CapabilityDenied, CapabilityFacade
from demiurge.runtime.context import ContextAssembler
from demiurge.core import CoreLoader, LoadedCore, SlotDefinition
from demiurge.runtime.child_agents import (
    CHILD_AGENT_ALL_TOOLS,
    ChildAgentRuntime,
    ChildSlotRequest,
    ChildToolRequest,
    ResolvedChildAgentTools,
    ResolvedPhaseSlots,
    RunnerChildAgentHost,
)
from demiurge.runtime.delivery import DeliveryRequest, DeliveryRouteContext
from demiurge.runtime.interactions import (
    InteractionDelivery,
    InteractionInbound,
    InteractionItem,
    InteractionOutbound,
    SessionInteractionRouter,
    SessionRouteBinding,
)
from demiurge.runtime.io import RunnerTurnIOHost, TurnIO
from demiurge.runtime.module_delivery import ModuleDeliveryRuntime, RunnerModuleDeliveryHost
from demiurge.runtime.outbox import DeliveryRuntime
from demiurge.runtime.session import SessionRuntime
from demiurge.runtime.slot_context import (
    ModuleIOClient,
    ModuleResultClient,
    ModuleStateStores,
    RunnerSlotContextHost,
    SlotContextRuntime,
)
from demiurge.runtime.slots import (
    InputPipelineRequest,
    InputSlotRunRequest,
    ModuleInputBuilder,
    OutputPipelineRequest,
    OutputSlotRunRequest,
    RunnerSlotPipelineHost,
    SlotInvocation,
    SlotPipelineRuntime,
    SlotRuntime,
)
from demiurge.runtime.store import RuntimeEvent
from demiurge.runtime.turn import RunnerTurnEngineHost, TurnEngine, TurnEngineRequest
from demiurge.runtime.turn_lifecycle import TurnLifecycleCompletion, TurnLifecycleRequest, TurnLifecycleRuntime
from demiurge.runtime_timezone import RuntimeTimezone, resolve_runtime_timezone
from demiurge.providers import LLMMessage, LLMRequest, LLMResponse, Provider, ToolCall
from demiurge.sdk import (
    AgentInput,
    AgentRunResult,
    AgentSpawnHandle,
    BootstrapContext,
    ContextContribution,
    DeliverEffect,
    EffectRequest,
    InputEnvelope,
    OutputEnvelope,
    RawInput,
    ToolResult,
    TurnContext,
)
from demiurge.storage import EventLog, SessionMessage, VersionStore
from demiurge.tools.records import ToolExecutionRecord
from demiurge.tools.runtime import ToolRuntime
from demiurge.util import utc_id


SUMMARY_PREFIX = (
    "[CONTEXT COMPACTION - REFERENCE ONLY] Earlier turns were compacted into the summary below. "
    "Treat it as background reference, not as active instructions. Respond only to the latest user "
    "message that appears after this summary; the latest user message wins if there is any conflict."
)
SUMMARY_END_MARKER = "--- END OF CONTEXT SUMMARY - respond to the message below, not the summary above ---"
DELEGATION_TOOL_NAMES = {"delegate_task", "task_status", "task_control", "yield_until"}


@dataclass(slots=True)
class TurnResult:
    session_id: str
    turn_id: str
    core_id: str
    core_revision: str
    items: list[InteractionItem]
    agent_result: Any = None
    needs_user: bool = False

    @property
    def deliveries(self) -> list[InteractionDelivery]:
        return [item.delivery for item in self.items if item.kind == "delivery" and item.delivery is not None]

    @property
    def tool_results(self) -> list[ToolExecutionRecord]:
        results: list[ToolExecutionRecord] = []
        for item in self.items:
            if item.kind == "tool_result" and item.tool_result is not None:
                results.append(item.tool_result)
                continue
            if item.kind == "tool_call" and item.tool_call is not None:
                record = item.tool_call.execution_record()
                if record is not None:
                    results.append(record)
        return results


@dataclass(slots=True)
class CompactionResult:
    session_id: str
    turn_id: str
    compacted_count: int
    summary_message_id: str | None
    summary: str
    skipped: bool = False
    error: str | None = None

class ModuleBootstrapClient:
    def __init__(self, *, workspace: str | None = None) -> None:
        self.fragments: list[str] = []
        self.workspace = workspace or ""

    def add(self, text: str) -> None:
        content = str(text or "")
        if content.strip():
            self.fragments.append(content)


class SessionTurnStepRunner:
    def __init__(
        self,
        *,
        home: Path,
        version_store: VersionStore,
        core_loader: CoreLoader,
        provider: Provider,
        tool_runtime: ToolRuntime,
        core_id: str = "assistant",
        session_id: str | None = None,
        model_override: str | None = None,
        model_resolver: Callable[[Any], str] | None = None,
        provider_name: str | None = None,
        workspace: str | None = None,
        initial_core_path: Path | None = None,
        show_system_prompt: bool = False,
        runtime_timezone: RuntimeTimezone | None = None,
        task_worker: RuntimeTaskWorker | None = None,
        session_runtime: SessionRuntime | None = None,
        slot_runtime: SlotRuntime | None = None,
        turn_engine: TurnEngine | None = None,
        interaction_router: SessionInteractionRouter | None = None,
        prepare_live_core: Callable[[], Awaitable[Any]] | None = None,
    ):
        self.home = home
        self.version_store = version_store
        self.core_loader = core_loader
        self.provider = provider
        self.tool_runtime = tool_runtime
        self.core_id = core_id
        self.session_id = session_id or utc_id("session_")
        self.model_override = model_override
        self.model_resolver = model_resolver
        self.provider_name = provider_name
        self.workspace = workspace
        self.initial_core_path = initial_core_path
        self.show_system_prompt = show_system_prompt
        self.runtime_timezone = runtime_timezone or resolve_runtime_timezone()
        self.task_worker = task_worker or getattr(tool_runtime, "task_worker", None)
        if self.task_worker is None:
            raise ValueError("SessionTurnStepRunner requires a RuntimeControlPlane-backed RuntimeTaskWorker")
        if session_runtime is None:
            raise ValueError("SessionTurnStepRunner requires a RuntimeControlPlane-backed SessionRuntime")
        self.session_runtime = session_runtime
        self.sessions = session_runtime
        self.slot_runtime = slot_runtime or SlotRuntime()
        self.child_agents = ChildAgentRuntime(RunnerChildAgentHost(self))
        self.slot_context = SlotContextRuntime(RunnerSlotContextHost(self))
        self.slot_pipeline = SlotPipelineRuntime(RunnerSlotPipelineHost(self))
        self.turn_engine = turn_engine or TurnEngine(RunnerTurnEngineHost(self))
        self.interaction_router = interaction_router or SessionInteractionRouter()
        self.prepare_live_core_callback = prepare_live_core
        self.context_assembler = ContextAssembler()
        self.event_log = EventLog(home, self.session_id)
        self.turn_lifecycle = TurnLifecycleRuntime(
            home=self.home,
            session_runtime=self.session_runtime,
            task_worker=self.task_worker,
            event_log=self.event_log,
        )
        self.delivery_runtime = DeliveryRuntime(
            store=self.session_runtime.store,
            event_log=self.event_log,
            router=self.interaction_router,
        )
        self.module_delivery = ModuleDeliveryRuntime(RunnerModuleDeliveryHost(self))
        self.runtime_io = TurnIO(RunnerTurnIOHost(self))
        self.history: list[LLMMessage] = []
        self.display_turns: list[dict[str, Any]] = []
        self._session_started_ids: set[str] = set()
        self._background_tasks: set[asyncio.Task[Any]] = set()
        self._ensure_current_session()

    def emit_turn_event(self, event_type: str, **payload: Any) -> dict[str, Any]:
        return self.event_log.emit(event_type, **payload)

    def emit_slot_event(self, event_type: str, **payload: Any) -> dict[str, Any]:
        return self.event_log.emit(event_type, **payload)

    def track_slot_background_task(self, task: asyncio.Task[Any]) -> None:
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    def _bind_event_log(self) -> None:
        self.event_log = EventLog(self.home, self.session_id)
        if hasattr(self, "delivery_runtime"):
            self.delivery_runtime.event_log = self.event_log
        if hasattr(self, "turn_lifecycle"):
            self.turn_lifecycle.event_log = self.event_log

    async def run_turn(
        self,
        text: str,
        *,
        core_path: Path | None = None,
        interaction: InteractionInbound | None = None,
        injected_system_context: list[str] | None = None,
        input_slot_ids: list[str] | tuple[str, ...] | None = None,
        output_slot_ids: list[str] | tuple[str, ...] | None = None,
        input_phase_slots: ResolvedPhaseSlots | None = None,
        output_phase_slots: ResolvedPhaseSlots | None = None,
        use_bootstrap: bool = True,
        route_binding: SessionRouteBinding | None = None,
    ) -> TurnResult:
        core = self.core_loader.load(core_path) if core_path is not None else await self.load_active_core()
        interaction_metadata = self._interaction_metadata(interaction)
        self._resolve_session_for_interaction(core, interaction_metadata)
        if route_binding is not None:
            route_binding.bind(self.interaction_router, self.session_id)
        if core_path is None:
            self.session_runtime.update_session(
                self.session_id,
                core_id=core.core_id,
                core_revision=self._core_revision(core),
                provider=self.provider_name,
                model=self._resolve_model_name(core),
                touch=False,
            )
        if input_slot_ids is not None and input_phase_slots is not None:
            raise ValueError("input_slot_ids and input_phase_slots cannot both be set")
        if output_slot_ids is not None and output_phase_slots is not None:
            raise ValueError("output_slot_ids and output_phase_slots cannot both be set")
        input_slots_override = self._resolve_phase_slots(core, "input", input_slot_ids)
        output_slots_override = self._resolve_phase_slots(core, "output", output_slot_ids)
        capability = CapabilityFacade(core)
        if not self._session_started:
            self.event_log.emit("session.started", core_id=core.core_id, core_revision=self._core_revision(core), **interaction_metadata)
            self._session_started_ids.add(self.session_id)
        if use_bootstrap:
            await self._ensure_bootstrap_context(core, capability, interaction_metadata=interaction_metadata)

        lifecycle = self.turn_lifecycle.begin(
            TurnLifecycleRequest(
                session_id=self.session_id,
                core_id=core.core_id,
                core_revision=self._core_revision(core),
                raw_text=text,
                metadata=interaction_metadata,
                attachments=tuple(interaction.attachments) if interaction is not None else (),
            )
        )
        turn_id = lifecycle.turn_id
        input_envelope = lifecycle.input_envelope
        state_stores = lifecycle.state_stores
        turn = lifecycle.turn

        try:
            user_text, persisted_user_text, context, input_items = await self._run_input_slots(
                core,
                turn,
                capability,
                input_envelope,
                state_stores,
                interaction_metadata=interaction_metadata,
                injected_system_context=injected_system_context or [],
                serial_slots=input_slots_override,
                phase_slots=input_phase_slots,
            )
        except asyncio.CancelledError:
            self.turn_lifecycle.interrupt(lifecycle, status="cancelled", error="turn cancelled")
            raise
        except Exception as exc:
            self.turn_lifecycle.interrupt(
                lifecycle,
                status="failed",
                error=self._sanitize_runtime_error(exc),
            )
            raise
        turn.user_input = AgentInput(content=user_text, metadata=interaction_metadata)
        self.event_log.emit("message.received", turn_id=turn_id, content=user_text, **interaction_metadata)
        if persisted_user_text:
            self.runtime_io.send_user(
                turn_id=turn_id,
                content=persisted_user_text,
                interaction_metadata=interaction_metadata,
            )
        items: list[InteractionItem] = list(input_items)
        await self.tool_runtime.prepare_for_turn(core, turn, emit_event=self.event_log.emit)
        available_tools = self.tool_runtime.definitions_for(core, turn=turn)
        try:
            engine_result = await self.turn_engine.run(
                TurnEngineRequest(
                    core=core,
                    turn=turn,
                    capability=capability,
                    context=context,
                    available_tools=available_tools,
                    interaction_metadata=interaction_metadata,
                    use_bootstrap_context=use_bootstrap,
                )
            )
        except asyncio.CancelledError:
            self.turn_lifecycle.interrupt(lifecycle, status="cancelled", error="turn cancelled")
            raise
        except Exception as exc:
            self.turn_lifecycle.interrupt(
                lifecycle,
                status="failed",
                error=self._sanitize_runtime_error(exc),
            )
            raise
        final_output = engine_result.final_output
        needs_user = engine_result.needs_user
        tool_records = engine_result.tool_records
        turn_messages = engine_result.turn_messages
        items.extend(engine_result.items)

        result_client = self._module_result_client(writable=True)
        try:
            output_items = await self._run_output_slots(
                core,
                turn,
                capability,
                current_output=final_output,
                tool_records=tool_records,
                state_stores=state_stores,
                interaction_metadata=interaction_metadata,
                result_client=result_client,
                serial_slots=output_slots_override,
                phase_slots=output_phase_slots,
            )
        except asyncio.CancelledError:
            self.turn_lifecycle.interrupt(lifecycle, status="cancelled", error="turn cancelled")
            raise
        except Exception as exc:
            self.turn_lifecycle.interrupt(
                lifecycle,
                status="failed",
                error=self._sanitize_runtime_error(exc),
            )
            raise
        items.extend(output_items)
        delivered_texts = [
            item.delivery.text
            for item in items
            if item.kind == "delivery" and item.delivery is not None and item.delivery.visible and item.delivery.text
        ]
        for text in delivered_texts:
            turn_messages.append(LLMMessage(role="assistant", content=text))
        self.history = self._session_history_messages()
        self.display_turns.append(
            {
                "turn_id": turn_id,
                "user": user_text,
                "assistant": delivered_texts,
                "tools": [
                    {
                        "name": record.call.name,
                        "content": record.result.content,
                        "display_output": record.result.display_output,
                        "is_error": record.result.is_error,
                    }
                    for record in tool_records
                ],
            }
        )
        self.turn_lifecycle.complete(
            lifecycle,
            TurnLifecycleCompletion(
                items=tuple(items),
                agent_result=result_client.value,
                needs_user=needs_user,
                result_ref=turn_id,
            ),
        )
        return TurnResult(
            session_id=self.session_id,
            turn_id=turn_id,
            core_id=core.core_id,
            core_revision=self._core_revision(core),
            items=items,
            agent_result=result_client.value,
            needs_user=needs_user,
        )

    @property
    def _session_started(self) -> bool:
        return self.session_id in self._session_started_ids

    async def _ensure_bootstrap_context(
        self,
        core: LoadedCore,
        capability: CapabilityFacade,
        *,
        interaction_metadata: dict[str, Any],
    ) -> None:
        if not core.bootstrap_enabled:
            return
        if self.sessions.bootstrap_context_exists(self.session_id):
            return

        self.event_log.emit(
            "bootstrap.started",
            core_id=core.core_id,
            core_revision=self._core_revision(core),
            slots=[slot.slot_id for slot in core.bootstrap_pipeline.serial],
            **interaction_metadata,
        )
        fragments: list[str] = []
        try:
            for slot in core.bootstrap_pipeline.serial:
                self.event_log.emit(
                    "bootstrap.module.started",
                    core_id=core.core_id,
                    core_revision=self._core_revision(core),
                    slot=slot.relative_path,
                    kind="bootstrap",
                    **interaction_metadata,
                )
                client = ModuleBootstrapClient(workspace=self.workspace)
                ctx = BootstrapContext(
                    session_id=self.session_id,
                    core_id=core.core_id,
                    core_revision=self._core_revision(core),
                    workspace=self.workspace or "",
                    slot_id=slot.slot_id,
                    slot_path=slot.relative_path,
                    capability=capability,
                    bootstrap=client,
                )
                try:
                    value = await self._call_slot(slot, ctx)
                    if value is not None:
                        self.event_log.emit(
                            "bootstrap.module.return_ignored",
                            core_id=core.core_id,
                            core_revision=self._core_revision(core),
                            slot=slot.relative_path,
                            kind="bootstrap",
                            **interaction_metadata,
                        )
                    fragments.extend(client.fragments)
                    self.event_log.emit(
                        "bootstrap.module.completed",
                        core_id=core.core_id,
                        core_revision=self._core_revision(core),
                        slot=slot.relative_path,
                        kind="bootstrap",
                        fragments=len(client.fragments),
                        chars=sum(len(fragment) for fragment in client.fragments),
                        **interaction_metadata,
                    )
                except Exception as exc:
                    self.event_log.emit(
                        "bootstrap.module.failed",
                        core_id=core.core_id,
                        core_revision=self._core_revision(core),
                        slot=slot.relative_path,
                        kind="bootstrap",
                        error=str(exc),
                        **interaction_metadata,
                    )
                    if slot.failure_policy == "hard":
                        raise
            content = "\n\n".join(fragments)
            self.sessions.write_bootstrap_context(self.session_id, content)
            self.event_log.emit(
                "bootstrap.completed",
                core_id=core.core_id,
                core_revision=self._core_revision(core),
                fragments=len(fragments),
                chars=len(content),
                **interaction_metadata,
            )
        except Exception as exc:
            self.event_log.emit(
                "bootstrap.failed",
                core_id=core.core_id,
                core_revision=self._core_revision(core),
                error=str(exc),
                **interaction_metadata,
            )
            raise

    def _build_messages(
        self,
        core: LoadedCore,
        context: list[ContextContribution],
        turn_messages: list[LLMMessage],
        *,
        turn_id: str,
        step_id: str,
        use_bootstrap_context: bool = True,
    ) -> list[LLMMessage]:
        assembled = self.context_assembler.assemble(
            core=core,
            context=context,
            session_history=[
                message
                for message in self.sessions.history_for_context(self.session_id)
                if message.turn_id != turn_id
            ],
            current_turn_messages=turn_messages,
            bootstrap_context=self.sessions.read_bootstrap_context(self.session_id) if use_bootstrap_context else None,
            compaction_summary=self.sessions.latest_compaction_summary(self.session_id),
        )
        self.event_log.emit(
            "context.assembled",
            turn_id=turn_id,
            step_id=step_id,
            layers=assembled.layer_summaries(),
            total_messages=len(assembled.messages),
            total_chars=sum(len(message.content or "") for message in assembled.messages),
        )
        return assembled.messages

    def build_turn_messages(
        self,
        core: LoadedCore,
        context: list[ContextContribution],
        turn_messages: list[LLMMessage],
        *,
        turn_id: str,
        step_id: str,
        use_bootstrap_context: bool = True,
    ) -> list[LLMMessage]:
        return self._build_messages(
            core,
            context,
            turn_messages,
            turn_id=turn_id,
            step_id=step_id,
            use_bootstrap_context=use_bootstrap_context,
        )

    async def _maybe_deliver_system_prompt_debug(
        self,
        messages: list[LLMMessage],
        *,
        turn: TurnContext,
        step_id: str,
        interaction_metadata: dict[str, Any],
    ) -> None:
        if not self.show_system_prompt:
            return
        system_messages = [
            message
            for message in messages
            if message.role == "system" and (message.content or "").strip()
        ]
        if not system_messages:
            self.event_log.emit(
                "debug.system_prompt.skipped",
                turn_id=turn.turn_id,
                step_id=step_id,
                reason="no_system_messages",
                **interaction_metadata,
            )
            return

        channel = interaction_metadata.get("channel")
        if not channel:
            self.event_log.emit(
                "debug.system_prompt.skipped",
                turn_id=turn.turn_id,
                step_id=step_id,
                reason="no_channel",
                system_messages=len(system_messages),
                total_chars=sum(len(message.content or "") for message in system_messages),
                **interaction_metadata,
            )
            return

        text = self._format_system_prompt_debug(system_messages, turn_id=turn.turn_id, step_id=step_id)
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
            session_id=self.session_id,
            turn_id=turn.turn_id,
            metadata=dict(interaction_metadata),
        )
        try:
            result = await self.interaction_router.deliver(outbound)
            self.event_log.emit(
                "debug.system_prompt.unrouted" if result.status == "unrouted" else "debug.system_prompt.delivered",
                turn_id=turn.turn_id,
                step_id=step_id,
                system_messages=len(system_messages),
                total_chars=sum(len(message.content or "") for message in system_messages),
                **interaction_metadata,
            )
        except Exception as exc:
            item.set_dispatch_status("failed")
            self.event_log.emit(
                "debug.system_prompt.failed",
                turn_id=turn.turn_id,
                step_id=step_id,
                error=str(exc),
                system_messages=len(system_messages),
                total_chars=sum(len(message.content or "") for message in system_messages),
                **interaction_metadata,
            )

    async def deliver_turn_system_prompt_debug(
        self,
        messages: list[LLMMessage],
        *,
        turn: TurnContext,
        step_id: str,
        interaction_metadata: dict[str, Any],
    ) -> None:
        await self._maybe_deliver_system_prompt_debug(
            messages,
            turn=turn,
            step_id=step_id,
            interaction_metadata=interaction_metadata,
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

    def _build_skill_index(self, core: LoadedCore) -> str:
        return self.context_assembler._build_skill_index(core)

    def _core_revision(self, core: LoadedCore) -> str:
        try:
            return self.version_store.active_pointer(core.core_id).active_revision
        except Exception:
            return "untracked"

    def _resolve_model_name(self, core: LoadedCore) -> str:
        if self.model_resolver:
            return self.model_resolver(core.manifest.model)
        if self.model_override:
            return self.model_override
        return core.manifest.model.model_name or "fake/demo"

    def resolve_turn_model_name(self, core: LoadedCore) -> str:
        return self._resolve_model_name(core)

    async def complete_turn_provider(self, request: LLMRequest) -> LLMResponse:
        return await self.provider.complete(request)

    async def prepare_live_core(self) -> None:
        if self.prepare_live_core_callback is not None:
            await self.prepare_live_core_callback()

    async def load_active_core(self) -> LoadedCore:
        await self.prepare_live_core()
        return self.core_loader.load(self.version_store.active_core_path(self.core_id))

    def start_new_session(
        self,
        *,
        channel: str | None = None,
        conversation_key: str | None = None,
        source: str | None = None,
        reply_to: str | None = None,
        replace_conversation_binding: bool = False,
    ) -> str:
        core = self.core_loader.load(self.version_store.active_core_path(self.core_id))
        core_revision = self._core_revision(core)
        metadata = {key: value for key, value in {"source": source, "reply_to": reply_to}.items() if value is not None}
        bind_immediately = not (replace_conversation_binding and channel and conversation_key)
        record = self.session_runtime.create_session(
            core_id=core.core_id,
            core_revision=core_revision,
            channel=channel if bind_immediately else None,
            conversation_key=conversation_key if bind_immediately else None,
            workspace=self.workspace,
            provider=self.provider_name,
            model=self._resolve_model_name(core),
            metadata=metadata,
        )
        if not bind_immediately:
            record = self.session_runtime.rebind_interaction_session(
                record.session_id,
                core_id=core.core_id,
                core_revision=core_revision,
                channel=channel,
                conversation_key=conversation_key,
                metadata=metadata,
            )
        self._switch_session(record.session_id, emit_resumed=False)
        self.event_log.emit(
            "session.created",
            core_id=core.core_id,
            core_revision=core_revision,
            channel=channel,
            conversation_key=conversation_key,
        )
        return record.session_id

    def resume_session(self, session_id: str) -> None:
        self._switch_session(session_id, emit_resumed=True)

    async def compact_session(self, *, focus: str | None = None, protect_last_n: int = 6) -> CompactionResult:
        core = await self.load_active_core()
        turn_id = utc_id("compact_")
        self.event_log.emit("session.compaction.started", turn_id=turn_id, focus=focus)
        try:
            messages = [
                message
                for message in self.sessions.history_for_context(self.session_id)
                if message.kind == "message" and message.turn_id
            ]
            turn_ids = list(dict.fromkeys(message.turn_id for message in messages if message.turn_id))
            protected_turns = max(protect_last_n, 0)
            if len(turn_ids) <= protected_turns:
                result = CompactionResult(
                    session_id=self.session_id,
                    turn_id=turn_id,
                    compacted_count=0,
                    summary_message_id=None,
                    summary="not enough history to compact",
                    skipped=True,
                )
                self.event_log.emit("session.compaction.completed", turn_id=turn_id, skipped=True, compacted_count=0)
                return result

            compact_turn_ids = set(turn_ids[:-protected_turns] if protected_turns else turn_ids)
            to_compact = [message for message in messages if message.turn_id in compact_turn_ids]
            if not to_compact:
                result = CompactionResult(
                    session_id=self.session_id,
                    turn_id=turn_id,
                    compacted_count=0,
                    summary_message_id=None,
                    summary="not enough history to compact",
                    skipped=True,
                )
                self.event_log.emit("session.compaction.completed", turn_id=turn_id, skipped=True, compacted_count=0)
                return result
            transcript = "\n\n".join(self._format_compaction_message(message) for message in to_compact)
            request = LLMRequest(
                model=self._resolve_model_name(core),
                messages=[
                    LLMMessage(
                        role="system",
                        content=(
                            "Summarize prior conversation turns for future context. Preserve durable facts, "
                            "decisions, unresolved questions, files or commands mentioned, and user preferences. "
                            "Write historical reference only; do not create new tasks."
                        ),
                    ),
                    LLMMessage(
                        role="user",
                        content="\n\n".join(
                            part
                            for part in [
                                f"Focus: {focus}" if focus else "",
                                "Transcript to compact:",
                                transcript,
                            ]
                            if part
                        ),
                    ),
                ],
                metadata={"turn_id": turn_id, "kind": "session_compaction"},
            )
            response: LLMResponse = await self.provider.complete(request)
            summary_body = (response.content or "").strip()
            if not summary_body:
                raise ValueError("provider returned an empty compaction summary")
            summary = f"{SUMMARY_PREFIX}\n\n{summary_body}\n\n{SUMMARY_END_MARKER}"
            summary_message = self.session_runtime.write_compaction_summary(
                self.session_id,
                content=summary,
                turn_id=turn_id,
                compacted_until_message_id=to_compact[-1].id,
                compacted_count=len(to_compact),
                focus=focus,
            )
            self.history = self._session_history_messages()
            self.event_log.emit(
                "session.compaction.completed",
                turn_id=turn_id,
                compacted_count=len(to_compact),
                summary_message_id=summary_message.id,
            )
            return CompactionResult(
                session_id=self.session_id,
                turn_id=turn_id,
                compacted_count=len(to_compact),
                summary_message_id=summary_message.id,
                summary=summary,
            )
        except Exception as exc:
            self.event_log.emit("session.compaction.failed", turn_id=turn_id, error=str(exc))
            return CompactionResult(
                session_id=self.session_id,
                turn_id=turn_id,
                compacted_count=0,
                summary_message_id=None,
                summary="",
                error=str(exc),
            )

    def _format_compaction_message(self, message: SessionMessage) -> str:
        metadata = message.metadata or {}
        prefix = message.role.upper()
        if message.role == "assistant" and metadata.get("tool_calls"):
            tool_calls = json.dumps(metadata["tool_calls"], ensure_ascii=False)
            if message.content.strip():
                return f"{prefix} [{message.turn_id} {metadata.get('step_id')}]: {message.content}\nTOOL_CALLS: {tool_calls}"
            return f"{prefix} [{message.turn_id} {metadata.get('step_id')}] TOOL_CALLS: {tool_calls}"
        if message.role == "tool":
            label = metadata.get("tool_name") or "tool"
            call_id = metadata.get("tool_call_id") or ""
            return f"TOOL {label} [{message.turn_id} {metadata.get('step_id')} {call_id}]: {message.content}"
        return f"{prefix} [{message.turn_id}]: {message.content}"

    def _ensure_current_session(self) -> None:
        core = self.core_loader.load(self.initial_core_path or self.version_store.active_core_path(self.core_id))
        _, created = self.session_runtime.ensure_session(
            self.session_id,
            core_id=core.core_id,
            core_revision=self._core_revision(core),
            workspace=self.workspace,
            provider=self.provider_name,
            model=self._resolve_model_name(core),
        )
        self._bind_event_log()
        self.event_log.emit(
            "session.created" if created else "session.resumed",
            core_id=core.core_id,
            core_revision=self._core_revision(core),
        )
        self.history = self._session_history_messages()

    def _switch_session(self, session_id: str, *, emit_resumed: bool) -> None:
        if not self.sessions.exists(session_id):
            raise FileNotFoundError(f"session not found: {session_id}")
        self.session_id = session_id
        self._bind_event_log()
        self.history = self._session_history_messages()
        if emit_resumed:
            record = self.sessions.get_session(session_id)
            self.event_log.emit(
                "session.resumed",
                core_id=record.core_id,
                core_revision=record.core_revision,
                channel=record.channel,
                conversation_key=record.conversation_key,
            )

    def _interaction_metadata(self, interaction: InteractionInbound | None) -> dict[str, Any]:
        metadata: dict[str, Any] = {}
        if interaction is not None:
            metadata.update(
                {
                    "channel": interaction.channel,
                    "source": interaction.source,
                    "reply_to": interaction.reply_to,
                    "conversation_key": interaction.conversation_key,
                    **dict(interaction.metadata or {}),
                }
            )
        metadata.update(self.runtime_timezone.metadata())
        return {key: value for key, value in metadata.items() if value is not None}

    def _resolve_session_for_interaction(self, core: LoadedCore, interaction_metadata: dict[str, Any]) -> None:
        channel = interaction_metadata.get("channel")
        conversation_key = interaction_metadata.get("conversation_key")
        if not channel:
            return
        if not conversation_key:
            if self.sessions.can_bind_session(
                self.session_id,
                channel=str(channel),
                conversation_key=None,
            ):
                self.session_runtime.update_session(
                    self.session_id,
                    core_id=core.core_id,
                    core_revision=self._core_revision(core),
                    channel=str(channel),
                    conversation_key=None,
                    metadata={
                        key: value
                        for key, value in interaction_metadata.items()
                        if key not in {"channel", "conversation_key"}
                    },
                )
            return
        existing = self.sessions.resolve_interaction_session(
            core_id=core.core_id,
            channel=str(channel),
            conversation_key=str(conversation_key),
        )
        if existing:
            if existing != self.session_id:
                self._switch_session(existing, emit_resumed=True)
            return
        if self.sessions.can_bind_session(
            self.session_id,
            channel=str(channel),
            conversation_key=str(conversation_key),
        ):
            self.session_runtime.update_session(
                self.session_id,
                core_id=core.core_id,
                core_revision=self._core_revision(core),
                channel=str(channel),
                conversation_key=str(conversation_key),
                metadata={
                    key: value for key, value in interaction_metadata.items() if key not in {"channel", "conversation_key"}
                },
            )
            return
        self.start_new_session(
            channel=str(channel),
            conversation_key=str(conversation_key),
            source=interaction_metadata.get("source"),
            reply_to=interaction_metadata.get("reply_to"),
        )

    def _resolve_phase_slots(
        self,
        core: LoadedCore,
        kind: str,
        slot_ids: list[str] | tuple[str, ...] | None,
    ) -> list[SlotDefinition] | None:
        if slot_ids is None:
            return None
        slots = core.input_slots if kind == "input" else core.output_slots
        by_id = {slot.slot_id: slot for slot in slots}
        resolved: list[SlotDefinition] = []
        seen: set[str] = set()
        for raw_id in slot_ids:
            slot_id = str(raw_id).strip()
            if not slot_id:
                raise ValueError(f"{kind} slot id must not be empty")
            if slot_id in seen:
                raise ValueError(f"duplicate {kind} slot id: {slot_id}")
            seen.add(slot_id)
            slot = by_id.get(slot_id)
            if slot is None:
                raise ValueError(f"unknown {kind} slot id: {slot_id}")
            resolved.append(slot)
        if not resolved:
            raise ValueError(f"{kind} slot list must not be empty")
        return resolved

    def _tool_result_model_content(self, result: ToolResult) -> str:
        return result.model_output if result.model_output is not None else result.content

    def turn_tool_result_model_content(self, result: ToolResult) -> str:
        return self._tool_result_model_content(result)

    def _truncate_model_content(self, content: str) -> str:
        if len(content) > 4000:
            return f"{content[:4000]}\n...[truncated {len(content) - 4000} chars]"
        return content

    def truncate_turn_model_content(self, content: str) -> str:
        return self._truncate_model_content(content)

    def _session_history_messages(self) -> list[LLMMessage]:
        messages: list[LLMMessage] = []
        for message in self.sessions.history_for_context(self.session_id):
            llm_message = self.context_assembler._session_message_to_llm(message)
            if llm_message is not None:
                messages.append(llm_message)
        return messages

    def _module_io_client(
        self,
        slot: SlotDefinition,
        *,
        turn: TurnContext,
        capability: CapabilityFacade,
        interaction_metadata: dict[str, Any],
        background: bool = False,
        items: list[InteractionItem] | None = None,
    ) -> ModuleIOClient:
        return self.slot_context.module_io_client(
            slot,
            turn=turn,
            capability=capability,
            interaction_metadata=interaction_metadata,
            background=background,
            items=items,
        )

    def turn_output_client(
        self,
        slot: SlotDefinition,
        *,
        turn: TurnContext,
        capability: CapabilityFacade,
        interaction_metadata: dict[str, Any],
        items: list[InteractionItem],
    ) -> ModuleIOClient:
        return self._module_io_client(
            slot,
            turn=turn,
            capability=capability,
            interaction_metadata=interaction_metadata,
            items=items,
        )

    async def send_turn_assistant_step(
        self,
        *,
        turn: TurnContext,
        step_id: str,
        content: str,
        tool_calls: list[ToolCall],
        interaction_metadata: dict[str, Any],
    ) -> tuple[SessionMessage | None, list[InteractionItem]]:
        return await self.runtime_io.send_assistant_step(
            turn=turn,
            step_id=step_id,
            content=content,
            tool_calls=tool_calls,
            interaction_metadata=interaction_metadata,
        )

    async def send_turn_tool_call_started(
        self,
        *,
        turn: TurnContext,
        step_id: str,
        call: ToolCall,
        interaction_metadata: dict[str, Any],
    ) -> InteractionItem:
        return await self.runtime_io.send_tool_call_started(
            turn=turn,
            step_id=step_id,
            call=call,
            interaction_metadata=interaction_metadata,
        )

    async def send_turn_tool_call_finished(
        self,
        *,
        turn: TurnContext,
        step_id: str,
        record: ToolExecutionRecord,
        interaction_metadata: dict[str, Any],
    ) -> InteractionItem:
        return await self.runtime_io.send_tool_call_finished(
            turn=turn,
            step_id=step_id,
            record=record,
            interaction_metadata=interaction_metadata,
        )

    def commit_module_delivery_request(
        self,
        request: DeliveryRequest,
        *,
        turn: TurnContext,
        slot: SlotDefinition,
        interaction_metadata: dict[str, Any],
    ) -> InteractionItem | None:
        item = self.runtime_io.send_module_output(
            request,
            turn=turn,
            slot=slot,
            interaction_metadata=interaction_metadata,
        )
        self.history = self._session_history_messages()
        return item

    def _commit_module_delivery_request(
        self,
        request: DeliveryRequest,
        *,
        turn: TurnContext,
        slot: SlotDefinition,
        interaction_metadata: dict[str, Any],
    ) -> InteractionItem | None:
        return self.commit_module_delivery_request(
            request,
            turn=turn,
            slot=slot,
            interaction_metadata=interaction_metadata,
        )

    def schedule_interaction_item(
        self,
        item: InteractionItem,
        *,
        turn: TurnContext,
        interaction_metadata: dict[str, Any],
    ) -> None:
        if item.dispatch_status != "pending":
            return
        metadata = self._interaction_item_outbound_metadata(interaction_metadata, item)
        channel = metadata.get("channel") or interaction_metadata.get("channel")
        if not channel:
            item.set_dispatch_status("unrouted")
            return
        item.set_dispatch_status("scheduled")
        self._enqueue_interaction_item(
            item,
            turn=turn,
            metadata=metadata,
            channel=str(channel),
        )

    def _schedule_interaction_item(
        self,
        item: InteractionItem,
        *,
        turn: TurnContext,
        interaction_metadata: dict[str, Any],
    ) -> None:
        self.schedule_interaction_item(
            item,
            turn=turn,
            interaction_metadata=interaction_metadata,
        )

    async def _dispatch_interaction_item_now(
        self,
        item: InteractionItem,
        *,
        turn: TurnContext,
        interaction_metadata: dict[str, Any],
    ) -> None:
        if item.dispatch_status != "pending":
            return
        metadata = self._interaction_item_outbound_metadata(interaction_metadata, item)
        channel = metadata.get("channel") or interaction_metadata.get("channel")
        if not channel:
            item.set_dispatch_status("unrouted")
            return
        item.set_dispatch_status("scheduled")
        await self.delivery_runtime.dispatch_item(
            item,
            session_id=self.session_id,
            turn_id=turn.turn_id,
            channel=str(channel),
            metadata=metadata,
            event_metadata=self._delivery_event_metadata(metadata),
        )

    def _schedule_slot_end_delivery_items(
        self,
        items: list[InteractionItem],
        *,
        turn: TurnContext,
        interaction_metadata: dict[str, Any],
    ) -> None:
        for item in items:
            self._schedule_interaction_item(
                item,
                turn=turn,
                interaction_metadata=interaction_metadata,
            )

    def _mark_slot_end_delivery_failed(self, items: list[InteractionItem], *, reason: str) -> None:
        for item in items:
            if item.delivery is None or item.dispatch_status != "pending":
                continue
            item.metadata["delivery_failed_reason"] = reason
            if item.delivery is not None:
                item.delivery.metadata = {
                    **dict(item.delivery.metadata),
                    "delivery_failed_reason": reason,
                }
            item.set_dispatch_status("failed")

    def _module_result_client(self, *, writable: bool) -> ModuleResultClient:
        return self.slot_context.result_client(writable=writable)

    async def _run_child_agent(
        self,
        *,
        core_id: str,
        raw_input: str,
        parent_turn: TurnContext,
        parent_slot_path: str,
        context: list[str],
        input_slots: ChildSlotRequest = None,
        output_slots: ChildSlotRequest = None,
        use_bootstrap: bool = False,
        tools: ChildToolRequest = CHILD_AGENT_ALL_TOOLS,
        session_id: str | None = None,
    ) -> AgentRunResult:
        return await self.child_agents.run_child(
            core_id=core_id,
            raw_input=raw_input,
            parent_turn=parent_turn,
            parent_slot_path=parent_slot_path,
            context=context,
            input_slots=input_slots,
            output_slots=output_slots,
            use_bootstrap=use_bootstrap,
            tools=tools,
            session_id=session_id,
        )

    def _spawn_child_agent(
        self,
        *,
        core_id: str,
        raw_input: str,
        parent_turn: TurnContext,
        parent_slot_path: str,
        context: list[str],
        input_slots: ChildSlotRequest = None,
        output_slots: ChildSlotRequest = None,
        use_bootstrap: bool = False,
        tools: ChildToolRequest = CHILD_AGENT_ALL_TOOLS,
        notify_on_complete: bool = True,
        session_id: str | None = None,
        resolved_child_tools: ResolvedChildAgentTools | None = None,
    ) -> AgentSpawnHandle:
        return self.child_agents.spawn_child(
            core_id=core_id,
            raw_input=raw_input,
            parent_turn=parent_turn,
            parent_slot_path=parent_slot_path,
            context=context,
            input_slots=input_slots,
            output_slots=output_slots,
            use_bootstrap=use_bootstrap,
            tools=tools,
            notify_on_complete=notify_on_complete,
            session_id=session_id,
            resolved_child_tools=resolved_child_tools,
        )

    async def execute_tool(
        self,
        call: ToolCall,
        *,
        core: LoadedCore,
        turn: TurnContext,
        capability: CapabilityFacade,
        emit_event: Callable[..., dict[str, Any]] | None = None,
        output_factory: Callable[[SlotDefinition], Any] | None = None,
    ) -> ToolResult:
        if call.name in DELEGATION_TOOL_NAMES:
            return await self.handle_delegation_tool(call, core=core, turn=turn, capability=capability)
        return await self.tool_runtime.execute(
            call,
            core=core,
            turn=turn,
            capability=capability,
            emit_event=emit_event,
            output_factory=output_factory,
        )

    async def execute_turn_tool(
        self,
        call: ToolCall,
        *,
        core: LoadedCore,
        turn: TurnContext,
        capability: CapabilityFacade,
        output_factory: Callable[[SlotDefinition], Any],
    ) -> ToolResult:
        return await self.execute_tool(
            call,
            core=core,
            turn=turn,
            capability=capability,
            emit_event=self.event_log.emit,
            output_factory=output_factory,
        )

    async def handle_delegation_tool(
        self,
        call: ToolCall,
        *,
        core: LoadedCore,
        turn: TurnContext,
        capability: CapabilityFacade,
    ) -> ToolResult:
        try:
            visible_tools = {entry.name for entry in self.tool_runtime.registry_for(core, turn=turn)}
            if call.name not in visible_tools:
                return ToolResult(content=f"builtin tool is not allowed: {call.name}", is_error=True)
            if call.name == "delegate_task":
                return await self._delegate_task(call, core=core, turn=turn, capability=capability)
            if call.name == "task_status":
                capability.require("task.control")
                return self._task_status(call)
            if call.name == "task_control":
                capability.require("task.control")
                return await self._task_control(call)
            if call.name == "yield_until":
                capability.require("task.control")
                return await self._yield_until(call)
            return ToolResult(content=f"unsupported delegation tool: {call.name}", is_error=True)
        except CapabilityDenied as exc:
            return ToolResult(content=str(exc), is_error=True, data={"executionStarted": False})

    async def _delegate_task(
        self,
        call: ToolCall,
        *,
        core: LoadedCore,
        turn: TurnContext,
        capability: CapabilityFacade,
    ) -> ToolResult:
        return await self.child_agents.handle_delegate_task(
            call,
            core=core,
            turn=turn,
            capability=capability,
        )

    def _delegation_context(self, context_mode: str) -> list[str]:
        return self.child_agents.delegation_context(context_mode)

    def _task_status(self, call: ToolCall) -> ToolResult:
        task_id = str(call.arguments.get("task_id") or "").strip()
        if not task_id:
            return ToolResult(content="task_id is required", is_error=True)
        view = str(call.arguments.get("view") or "model").strip()
        payload = self._task_view(task_id, include_log=view in {"operator", "debug"})
        if payload is None:
            return ToolResult(content=f"background task not found: {task_id}", is_error=True)
        content = json.dumps(payload, ensure_ascii=False)
        return ToolResult(content=content, data=payload, model_output=content)

    async def _task_control(self, call: ToolCall) -> ToolResult:
        task_id = str(call.arguments.get("task_id") or "").strip()
        if not task_id:
            return ToolResult(content="task_id is required", is_error=True)
        command = str(call.arguments.get("command") or "cancel").strip()
        if command != "cancel":
            return ToolResult(content=f"unsupported task_control command: {command}", is_error=True)
        try:
            record = await self.task_worker.cancel(task_id)
            payload = record.to_payload(include_log=True, log=self.task_worker.log(task_id))
        except (KeyError, RuntimeTaskKindError):
            return ToolResult(content=f"background task not found: {task_id}", is_error=True)
        return ToolResult(content=json.dumps(payload, ensure_ascii=False), data=payload)

    async def _yield_until(self, call: ToolCall) -> ToolResult:
        task_id = str(call.arguments.get("task_id") or "").strip()
        if not task_id:
            return ToolResult(content="task_id is required", is_error=True)
        raw_timeout = call.arguments.get("timeout_seconds")
        timeout = float(raw_timeout if raw_timeout is not None else 30)
        try:
            record = await self.task_worker.wait(task_id, timeout_seconds=timeout, consume_completion=True)
        except (KeyError, RuntimeTaskKindError):
            return ToolResult(content=f"background task not found: {task_id}", is_error=True)
        except asyncio.TimeoutError:
            payload = self._task_view(task_id, include_log=False) or {"task_id": task_id, "status": "unknown"}
            payload["timed_out"] = True
            return ToolResult(content=json.dumps(payload, ensure_ascii=False), data=payload)
        payload = record.to_payload(include_log=True, log=self.task_worker.log(task_id))
        return ToolResult(content=json.dumps(payload, ensure_ascii=False), data=payload)

    def _task_view(self, task_id: str, *, include_log: bool) -> dict[str, Any] | None:
        try:
            record = self.task_worker.get(task_id)
        except (KeyError, RuntimeTaskKindError):
            return None
        return record.to_payload(include_log=include_log, log=self.task_worker.log(task_id) if include_log else None)

    async def _run_input_slots(
        self,
        core: LoadedCore,
        turn: TurnContext,
        capability: CapabilityFacade,
        envelope: InputEnvelope,
        state_stores: ModuleStateStores,
        *,
        interaction_metadata: dict[str, Any],
        injected_system_context: list[str],
        serial_slots: list[SlotDefinition] | None = None,
        phase_slots: ResolvedPhaseSlots | None = None,
    ) -> tuple[str, str, list[ContextContribution], list[InteractionItem]]:
        if phase_slots is not None:
            parallel_slots = phase_slots.parallel
            current_serial_slots = phase_slots.serial
        else:
            parallel_slots = [] if serial_slots is not None else core.input_pipeline.parallel
            current_serial_slots = serial_slots or core.input_pipeline.serial
        result = await self.slot_pipeline.run_input(
            InputPipelineRequest(
                core=core,
                turn=turn,
                capability=capability,
                envelope=envelope,
                state_stores=state_stores,
                interaction_metadata=interaction_metadata,
                injected_system_context=injected_system_context,
                serial_slots=current_serial_slots,
                parallel_slots=parallel_slots,
            )
        )
        return result.user_text, result.persisted_user_text, result.context, result.items

    async def run_input_pipeline_slot(self, request: InputSlotRunRequest) -> list[InteractionItem]:
        return await self._run_input_slot(
            request.slot,
            core=request.core,
            turn=request.turn,
            capability=request.capability,
            envelope=request.envelope,
            raw_input=request.raw_input,
            builder=request.builder,
            builder_writable=request.builder_writable,
            state_stores=request.state_stores,
            interaction_metadata=request.interaction_metadata,
            activated=request.activated,
            contributions=request.contributions,
            background=request.background,
        )

    async def _run_input_slot(
        self,
        slot: SlotDefinition,
        *,
        core: LoadedCore,
        turn: TurnContext,
        capability: CapabilityFacade,
        envelope: InputEnvelope,
        raw_input: RawInput,
        builder: ModuleInputBuilder,
        builder_writable: bool,
        state_stores: ModuleStateStores,
        interaction_metadata: dict[str, Any],
        activated: set[str],
        contributions: list[ContextContribution],
        background: bool = False,
    ) -> list[InteractionItem]:
        items: list[InteractionItem] = []
        context_build = self.slot_context.build_input_context(
            InputSlotRunRequest(
                slot=slot,
                core=core,
                turn=turn,
                capability=capability,
                envelope=envelope,
                raw_input=raw_input,
                builder=builder,
                builder_writable=builder_writable,
                state_stores=state_stores,
                interaction_metadata=interaction_metadata,
                activated=activated,
                contributions=contributions,
                background=background,
            ),
            items=items,
        )
        ctx = context_build.context
        io_client = context_build.io_client
        try:
            prior_envelope_activations = set(envelope.activated_skills)
            value = await self._call_slot(slot, ctx)
            if value is not None:
                self.event_log.emit("module.return_ignored", turn_id=turn.turn_id, slot=slot.relative_path, kind="input")
            for name in [name for name in envelope.activated_skills if name not in prior_envelope_activations]:
                if name in activated:
                    continue
                self._require_skill_activation(capability, slot.relative_path, name)
                skill = core.skill_by_id(name)
                if skill is None:
                    activated.add(name)
                    self.event_log.emit("skill.activation_ignored", turn_id=turn.turn_id, slot=slot.relative_path, skill=name)
                    continue
                activated.add(name)
                contributions.append(
                    ContextContribution(type="skill", key=skill.name, content=skill.content, placement="system_context")
                )
                self.event_log.emit("skill.activated", turn_id=turn.turn_id, slot=slot.relative_path, skill=skill.name)
            self._schedule_slot_end_delivery_items(
                io_client.slot_end_items,
                turn=turn,
                interaction_metadata=interaction_metadata,
            )
            self.event_log.emit("module.completed", turn_id=turn.turn_id, slot=slot.relative_path, kind="input")
            return io_client.items
        except Exception as exc:
            self._mark_slot_end_delivery_failed(io_client.slot_end_items, reason="slot_failed")
            self.event_log.emit(
                "module.failed",
                turn_id=turn.turn_id,
                slot=slot.relative_path,
                kind="input",
                error=str(exc),
            )
            if slot.failure_policy == "hard":
                raise
            return io_client.items

    def _require_skill_activation(self, capability: CapabilityFacade, slot_path: str, name: str) -> None:
        scoped = f"skill.activate:{name}"
        if capability.can(scoped, slot_path=slot_path):
            capability.require(scoped, slot_path=slot_path)
            return
        capability.require("skill.activate", slot_path=slot_path)

    async def _run_output_slots(
        self,
        core: LoadedCore,
        turn: TurnContext,
        capability: CapabilityFacade,
        *,
        current_output: str,
        tool_records: list[ToolExecutionRecord],
        state_stores: ModuleStateStores,
        interaction_metadata: dict[str, Any],
        result_client: ModuleResultClient,
        serial_slots: list[SlotDefinition] | None = None,
        phase_slots: ResolvedPhaseSlots | None = None,
    ) -> list[InteractionItem]:
        if phase_slots is not None:
            parallel_slots = phase_slots.parallel
            current_serial_slots = phase_slots.serial
        else:
            parallel_slots = [] if serial_slots is not None else core.output_pipeline.parallel
            current_serial_slots = serial_slots or core.output_pipeline.serial
        return await self.slot_pipeline.run_output(
            OutputPipelineRequest(
                core=core,
                turn=turn,
                capability=capability,
                current_output=current_output,
                tool_records=tool_records,
                state_stores=state_stores,
                interaction_metadata=interaction_metadata,
                result_client=result_client,
                serial_slots=current_serial_slots,
                parallel_slots=parallel_slots,
            )
        )

    async def run_output_pipeline_slot(self, request: OutputSlotRunRequest) -> list[InteractionItem]:
        return await self._run_output_slot(
            request.slot,
            core=request.core,
            turn=request.turn,
            capability=request.capability,
            envelope=request.envelope,
            current_output=request.current_output,
            tool_records=request.tool_records,
            state_stores=request.state_stores,
            interaction_metadata=request.interaction_metadata,
            result_client=request.result_client,
            background=request.background,
        )

    async def _run_output_slot(
        self,
        slot: SlotDefinition,
        *,
        core: LoadedCore,
        turn: TurnContext,
        capability: CapabilityFacade,
        envelope: OutputEnvelope,
        current_output: str,
        tool_records: list[ToolExecutionRecord],
        state_stores: ModuleStateStores,
        interaction_metadata: dict[str, Any],
        result_client: ModuleResultClient,
        background: bool = False,
    ) -> list[InteractionItem]:
        items: list[InteractionItem] = []
        context_build = self.slot_context.build_output_context(
            OutputSlotRunRequest(
                slot=slot,
                core=core,
                turn=turn,
                capability=capability,
                envelope=envelope,
                current_output=current_output,
                tool_records=tool_records,
                state_stores=state_stores,
                interaction_metadata=interaction_metadata,
                result_client=result_client,
                background=background,
            ),
            items=items,
        )
        ctx = context_build.context
        io_client = context_build.io_client
        try:
            value = await self._call_slot(slot, ctx)
            if value is not None:
                self.event_log.emit("module.return_ignored", turn_id=turn.turn_id, slot=slot.relative_path, kind="output")
            self._schedule_slot_end_delivery_items(
                io_client.slot_end_items,
                turn=turn,
                interaction_metadata=interaction_metadata,
            )
            self.history = self._session_history_messages()
            self.event_log.emit(
                "module.completed",
                turn_id=turn.turn_id,
                slot=slot.relative_path,
                kind="output",
            )
            return io_client.items
        except Exception as exc:
            self._mark_slot_end_delivery_failed(io_client.slot_end_items, reason="slot_failed")
            self.event_log.emit(
                "module.failed",
                turn_id=turn.turn_id,
                slot=slot.relative_path,
                kind="output",
                error=str(exc),
            )
            if slot.failure_policy == "hard":
                raise
            return io_client.items

    async def flush_slot_background_items(
        self,
        items: list[InteractionItem],
        *,
        turn: TurnContext,
        interaction_metadata: dict[str, Any],
    ) -> None:
        await self._flush_pending_background_items(
            items,
            turn=turn,
            interaction_metadata=interaction_metadata,
        )

    def _delivery_route_context(
        self,
        turn: TurnContext,
        slot: SlotDefinition,
        interaction_metadata: dict[str, Any],
    ) -> DeliveryRouteContext:
        return DeliveryRouteContext(
            session_id=self.session_id,
            turn_id=turn.turn_id,
            channel=interaction_metadata.get("channel"),
            conversation_key=interaction_metadata.get("conversation_key"),
            source=interaction_metadata.get("source"),
            reply_to=interaction_metadata.get("reply_to"),
            slot=slot.relative_path,
            metadata=dict(interaction_metadata),
        )

    def _enqueue_interaction_item(
        self,
        item: InteractionItem,
        *,
        turn: TurnContext,
        metadata: dict[str, Any],
        channel: str,
    ) -> None:
        task = asyncio.create_task(
            self.delivery_runtime.dispatch_item(
                item,
                session_id=self.session_id,
                turn_id=turn.turn_id,
                channel=channel,
                metadata=metadata,
                event_metadata=self._delivery_event_metadata(metadata),
            )
        )
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _flush_pending_background_items(
        self,
        items: list[InteractionItem],
        *,
        turn: TurnContext,
        interaction_metadata: dict[str, Any],
    ) -> None:
        for item in items:
            if item.dispatch_status != "pending":
                continue
            metadata = self._interaction_item_outbound_metadata(interaction_metadata, item)
            channel = metadata.get("channel") or interaction_metadata.get("channel")
            if not channel:
                item.set_dispatch_status("unrouted")
                continue
            item.set_dispatch_status("scheduled")
            await self.delivery_runtime.dispatch_item(
                item,
                session_id=self.session_id,
                turn_id=turn.turn_id,
                channel=str(channel),
                metadata=metadata,
                event_metadata=self._delivery_event_metadata(metadata),
            )

    async def _handle_effects(
        self,
        effects: list[EffectRequest | dict[str, Any]],
        *,
        core: LoadedCore,
        turn: TurnContext,
        capability: CapabilityFacade,
        slot: SlotDefinition,
        interaction_metadata: dict[str, Any],
    ) -> list[InteractionDelivery]:
        deliveries: list[InteractionDelivery] = []
        for raw_effect in effects:
            effect = self._normalize_effect(raw_effect)
            try:
                if effect.type == "append_assistant_message" and effect.content:
                    effect = EffectRequest(
                        type="deliver",
                        payload={"type": "text", "text": effect.content},
                        visible=effect.visible,
                        history_policy=effect.history_policy,
                    )
                if effect.type == "deliver":
                    delivery = self._apply_deliver_effect(
                        effect,
                        turn=turn,
                        slot=slot,
                        interaction_metadata=interaction_metadata,
                    )
                    if delivery:
                        deliveries.append(delivery)
                elif effect.type == "append_assistant_message" and effect.content:
                    if effect.visible:
                        deliveries.append(
                            InteractionDelivery(
                                type="text",
                                text=effect.content,
                                payload={"type": "text", "text": effect.content},
                                visible=True,
                                history_policy=effect.history_policy or slot.history_policy,
                                metadata={"slot": slot.relative_path},
                            )
                        )
                elif effect.type == "evolve_core":
                    capability.require("tool.call:evolve_core", slot_path=slot.relative_path)
                    result = await self.tool_runtime._execute_builtin(
                        ToolCall(name="evolve_core", arguments={"goal": effect.goal or effect.reason or ""}),
                        core=core,
                        turn=turn,
                        capability=capability,
                    )
                    deliveries.append(
                        InteractionDelivery(
                            type="text",
                            text=result.content,
                            payload={"type": "text", "text": result.content},
                            metadata={"slot": slot.relative_path, "effect": effect.type},
                        )
                    )
                elif effect.type == "tool_call" and effect.tool_name:
                    capability.require(f"tool.call:{effect.tool_name}", slot_path=slot.relative_path)
                    result = await self.execute_tool(
                        ToolCall(name=effect.tool_name, arguments=dict(effect.arguments or {})),
                        core=core,
                        turn=turn,
                        capability=capability,
                        emit_event=self.event_log.emit,
                    )
                    if result.content:
                        deliveries.append(
                            InteractionDelivery(
                                type="text",
                                text=result.content,
                                payload={"type": "text", "text": result.content},
                                metadata={"slot": slot.relative_path, "effect": effect.type},
                            )
                        )
                else:
                    self.event_log.emit(
                        "effect.ignored",
                        turn_id=turn.turn_id,
                        slot=slot.relative_path,
                        effect_type=effect.type,
                    )
            except CapabilityDenied as exc:
                self.event_log.emit(
                    "capability.denied",
                    turn_id=turn.turn_id,
                    slot=slot.relative_path,
                    error=str(exc),
                )
        return deliveries

    def _apply_delivery_request(
        self,
        request: DeliveryRequest,
        *,
        turn: TurnContext,
        slot: SlotDefinition,
        interaction_metadata: dict[str, Any],
    ) -> InteractionDelivery | None:
        return self.module_delivery.apply_request(
            request,
            turn=turn,
            slot=slot,
            interaction_metadata=interaction_metadata,
        )

    def _apply_deliver_effect(
        self,
        effect: EffectRequest,
        *,
        turn: TurnContext,
        slot: SlotDefinition,
        interaction_metadata: dict[str, Any],
    ) -> InteractionDelivery | None:
        request = self.module_delivery.request_from_deliver_effect(effect, slot=slot)
        item = self.runtime_io.send_module_output(
            request,
            turn=turn,
            slot=slot,
            interaction_metadata=interaction_metadata,
        )
        return item.delivery if item is not None else None

    async def drain_background_tasks(self, *, include_task_worker: bool = True) -> None:
        while self._background_tasks:
            await asyncio.gather(*list(self._background_tasks), return_exceptions=True)
        if include_task_worker:
            await self.task_worker.drain()

    @property
    def background_task_count(self) -> int:
        return sum(1 for task in self._background_tasks if not task.done()) + self.task_worker.active_count

    def _append_runtime_event(self, event: RuntimeEvent) -> None:
        self._append_runtime_events([event])

    def append_turn_runtime_event(self, event: RuntimeEvent) -> None:
        self._append_runtime_event(event)

    def _append_runtime_events(self, events: list[RuntimeEvent]) -> None:
        control_plane = getattr(self.session_runtime, "control_plane", None)
        if control_plane is not None:
            control_plane.record_events(events)

    def _sanitize_runtime_error(self, exc: Exception) -> str:
        message = str(exc).replace("\n", " ").strip()
        if len(message) > 500:
            message = f"{message[:500]}... [truncated]"
        return f"{exc.__class__.__name__}: {message}" if message else exc.__class__.__name__

    def _interaction_item_outbound_metadata(
        self,
        interaction_metadata: dict[str, Any],
        item: InteractionItem,
    ) -> dict[str, Any]:
        if item.delivery is not None:
            return self._background_outbound_metadata(interaction_metadata, [item.delivery])
        metadata = dict(interaction_metadata)
        for key in ("phase", "step_id", "tool_name", "tool_call_id", "is_error", "dispatch_status"):
            if item.metadata.get(key) is not None:
                metadata[key] = item.metadata[key]
        return metadata

    def _background_outbound_metadata(
        self,
        interaction_metadata: dict[str, Any],
        deliveries: list[InteractionDelivery],
    ) -> dict[str, Any]:
        metadata = dict(interaction_metadata)
        if not deliveries:
            return metadata
        delivery_metadata = deliveries[0].metadata
        route = delivery_metadata.get("route")
        if isinstance(route, dict):
            for key in ("session_id", "turn_id", "channel", "conversation_key", "source", "reply_to"):
                if route.get(key) is not None:
                    metadata.setdefault(key, route.get(key))
        for key in ("slot", "phase", "delivery_id", "kind", "history_policy", "delivery", "delivery_status", "background"):
            if delivery_metadata.get(key) is not None:
                metadata[key] = delivery_metadata[key]
        return metadata

    def _delivery_event_metadata(self, metadata: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in metadata.items() if key != "turn_id"}

    async def _call_slot(self, slot: SlotDefinition, ctx: Any) -> Any:
        outcome = await self.slot_runtime.invoke(SlotInvocation(slot=slot, context=ctx, phase=slot.kind))
        outcome.raise_for_error()
        return outcome.value

    def _normalize_context_items(
        self,
        items: list[ContextContribution | dict[str, Any]],
        *,
        default_placement: str = "pre_current_user",
    ) -> list[ContextContribution]:
        result: list[ContextContribution] = []
        for item in items:
            if isinstance(item, ContextContribution):
                if not item.placement:
                    item.placement = default_placement
                result.append(item)
            elif isinstance(item, dict):
                data = dict(item)
                data.setdefault("placement", default_placement)
                result.append(ContextContribution(**data))
        return result

    def _normalize_effect(self, value: EffectRequest | dict[str, Any]) -> EffectRequest:
        if isinstance(value, DeliverEffect):
            return EffectRequest(
                type="deliver",
                payload=value.payload,
                attachments=list(value.attachments),
                visible=value.visible,
                history_policy=value.history_policy,
                target=value.target,
            )
        if isinstance(value, EffectRequest):
            return value
        return EffectRequest(**value)
