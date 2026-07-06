from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping

from demiurge.runtime.tasks import (
    RuntimeTaskWorker,
)
from demiurge.security.capabilities import CapabilityDenied, CapabilityFacade
from demiurge.runtime.bootstrap import BootstrapSlotRuntime, RunnerBootstrapSlotHost
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
from demiurge.runtime.delegation_tools import DelegationToolRuntime, RunnerDelegationToolHost
from demiurge.runtime.interaction_dispatch import InteractionDispatchRuntime
from demiurge.runtime.interactions import (
    InteractionDelivery,
    InteractionInbound,
    InteractionItem,
    SessionInteractionRouter,
    SessionRouteBinding,
)
from demiurge.runtime.io import RunnerTurnIOHost, TurnIO
from demiurge.runtime.module_delivery import ModuleDeliveryRuntime, RunnerModuleDeliveryHost
from demiurge.runtime.outbox import DeliveryRuntime
from demiurge.runtime.prompt_context import PromptBuildRequest, PromptContextRuntime, PromptDebugRequest
from demiurge.runtime.session_compaction import CompactionResult, SessionCompactionRuntime
from demiurge.runtime.session import SessionRuntime
from demiurge.runtime.slot_execution import SlotExecutionRuntime
from demiurge.runtime.slot_effects import SlotEffectRuntime
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
    OutputPipelineRequest,
    OutputSlotRunRequest,
    RunnerSlotPipelineHost,
    SlotPipelineRuntime,
    SlotRuntime,
)
from demiurge.runtime.store import RuntimeEvent
from demiurge.runtime.turn import RunnerTurnEngineHost, TurnEngine
from demiurge.runtime.turn_lifecycle import TurnLifecycleRuntime
from demiurge.runtime.turn_pipeline import RunnerTurnPipelineHost, TurnPipelineRequest, TurnPipelineRuntime, TurnResult
from demiurge.runtime_timezone import RuntimeTimezone, resolve_runtime_timezone
from demiurge.providers import LLMMessage, LLMRequest, LLMResponse, Provider, ToolCall
from demiurge.sdk import (
    AgentRunResult,
    AgentSpawnHandle,
    ContextContribution,
    DeliverEffect,
    EffectRequest,
    InputEnvelope,
    ToolResult,
    TurnContext,
)
from demiurge.storage import EventLog, SessionMessage, VersionStore
from demiurge.tools.records import ToolExecutionRecord
from demiurge.tools.runtime import ToolRuntime
from demiurge.util import utc_id


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
        self.delegation_tools = DelegationToolRuntime(RunnerDelegationToolHost(self))
        self.bootstrap_slots = BootstrapSlotRuntime(RunnerBootstrapSlotHost(self))
        self.slot_pipeline = SlotPipelineRuntime(RunnerSlotPipelineHost(self))
        self.turn_engine = turn_engine or TurnEngine(RunnerTurnEngineHost(self))
        self.interaction_router = interaction_router or SessionInteractionRouter()
        self.prepare_live_core_callback = prepare_live_core
        self.context_assembler = ContextAssembler()
        self.event_log = EventLog(home, self.session_id)
        self.prompt_context = PromptContextRuntime(
            assembler=self.context_assembler,
            sessions=self.sessions,
            interaction_router=self.interaction_router,
            session_id=lambda: self.session_id,
            show_system_prompt=lambda: self.show_system_prompt,
            emit_event=lambda event_type, **payload: self.event_log.emit(event_type, **payload),
        )
        self.session_compaction = SessionCompactionRuntime(
            sessions=self.sessions,
            session_id=lambda: self.session_id,
            load_core=self.load_active_core,
            resolve_model_name=self._resolve_model_name,
            complete_provider=self.complete_turn_provider,
            emit_event=lambda event_type, **payload: self.event_log.emit(event_type, **payload),
            refresh_history=self._refresh_history,
        )
        self.history: list[LLMMessage] = []
        self.display_turns: list[dict[str, Any]] = []
        self._session_started_ids: set[str] = set()
        self._background_tasks: set[asyncio.Task[Any]] = set()
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
        self.interaction_dispatch = InteractionDispatchRuntime(
            session_id=lambda: self.session_id,
            delivery_runtime=self.delivery_runtime,
            track_background_task=self.track_slot_background_task,
        )
        self.slot_effects = SlotEffectRuntime(
            home=self.home,
            session_id=lambda: self.session_id,
            workspace=self.workspace,
            module_delivery=self.module_delivery,
            dispatch=self.interaction_dispatch,
            on_history_changed=self._refresh_history,
        )
        self.slot_context = SlotContextRuntime(RunnerSlotContextHost(self), effects=self.slot_effects)
        self.slot_execution = SlotExecutionRuntime(
            slot_runtime=self.slot_runtime,
            slot_context=self.slot_context,
            slot_effects=self.slot_effects,
            emit_event=self.event_log.emit,
            refresh_history=self._refresh_history,
        )
        self.runtime_io = TurnIO(RunnerTurnIOHost(self))
        self.turn_pipeline = TurnPipelineRuntime(RunnerTurnPipelineHost(self))
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
        return await self.turn_pipeline.run(
            TurnPipelineRequest(
                text=text,
                core_path=core_path,
                interaction=interaction,
                injected_system_context=injected_system_context,
                input_slot_ids=input_slot_ids,
                output_slot_ids=output_slot_ids,
                input_phase_slots=input_phase_slots,
                output_phase_slots=output_phase_slots,
                use_bootstrap=use_bootstrap,
                route_binding=route_binding,
            )
        )

    @property
    def _session_started(self) -> bool:
        return self.session_id in self._session_started_ids

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
        return self.prompt_context.build_messages(
            PromptBuildRequest(
                core=core,
                context=context,
                turn_messages=turn_messages,
                turn_id=turn_id,
                step_id=step_id,
                use_bootstrap_context=use_bootstrap_context,
            )
        )

    async def deliver_turn_system_prompt_debug(
        self,
        messages: list[LLMMessage],
        *,
        turn: TurnContext,
        step_id: str,
        interaction_metadata: dict[str, Any],
    ) -> None:
        await self.prompt_context.deliver_system_prompt_debug(
            PromptDebugRequest(
                messages=messages,
                turn=turn,
                step_id=step_id,
                interaction_metadata=interaction_metadata,
            )
        )

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
        return await self.session_compaction.compact(focus=focus, protect_last_n=protect_last_n)

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

    def _refresh_history(self) -> None:
        self.history = self._session_history_messages()

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
        if self.delegation_tools.can_handle(call.name):
            return await self.delegation_tools.execute(call, core=core, turn=turn, capability=capability)
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
        return await self.slot_execution.run_input(request)

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
        return await self.slot_execution.run_output(request)

    async def flush_slot_background_items(
        self,
        items: list[InteractionItem],
        *,
        turn: TurnContext,
        interaction_metadata: dict[str, Any],
    ) -> None:
        await self.slot_effects.flush_background_items(
            items,
            turn=turn,
            interaction_metadata=interaction_metadata,
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
                    delivery = self.slot_effects.apply_deliver_effect(
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
