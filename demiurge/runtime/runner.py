from __future__ import annotations

from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping

from demiurge.runtime.tasks import (
    RuntimeTaskWorker,
)
from demiurge.runtime.background import BackgroundWorkRuntime
from demiurge.security.capabilities import CapabilityFacade
from demiurge.runtime.bootstrap import BootstrapSlotRuntime, RunnerBootstrapSlotHost
from demiurge.runtime.context import ContextAssembler
from demiurge.core import CoreLoader, LoadedCore, SlotDefinition
from demiurge.runtime.child_agents import (
    ChildAgentRuntime,
    RunnerChildAgentHost,
)
from demiurge.runtime.delegation_tools import DelegationToolRuntime, RunnerDelegationToolHost
from demiurge.runtime.interaction_dispatch import InteractionDispatchRuntime
from demiurge.runtime.interactions import (
    InteractionInbound,
    SessionInteractionRouter,
    SessionRouteBinding,
)
from demiurge.runtime.io import RunnerTurnIOHost, TurnIO
from demiurge.runtime.module_delivery import ModuleDeliveryRuntime, RunnerModuleDeliveryHost
from demiurge.runtime.outbox import DeliveryRuntime
from demiurge.runtime.prompt_context import PromptBuildRequest, PromptContextRuntime, PromptDebugRequest
from demiurge.runtime.scope import AuthorityKind, PrincipalScope, PrincipalScopeResolver
from demiurge.runtime.session_compaction import CompactionResult, SessionCompactionRuntime
from demiurge.runtime.session import SessionRuntime
from demiurge.runtime.session_routing import SessionCoreBinding, SessionRoutingRuntime
from demiurge.runtime.slot_effects import SlotEffectRuntime
from demiurge.runtime.slot_context import (
    ModuleResultClient,
    RunnerSlotContextHost,
    SlotContextRuntime,
)
from demiurge.runtime.slots import (
    ResolvedPhaseSlots,
    SlotPipelineRuntime,
    SlotRuntime,
)
from demiurge.runtime.store import RuntimeEvent
from demiurge.runtime.turn import RunnerTurnEngineHost, TurnEngine
from demiurge.runtime.turn_lifecycle import TurnLifecycleRuntime
from demiurge.runtime.turn_pipeline import (
    RunnerTurnAdmissionHost,
    RunnerTurnPersistenceHost,
    RunnerTurnPipelineHost,
    TurnAdmissionRuntime,
    TurnExecutionContext,
    TurnPersistenceRuntime,
    TurnRequest,
    TurnExecution,
    TurnResult,
)
from demiurge.runtime_timezone import RuntimeTimezone, resolve_runtime_timezone
from demiurge.providers import LLMMessage, LLMRequest, LLMResponse, Provider, ToolCall
from demiurge.sdk import (
    ContextContribution,
    ToolResult,
    TurnContext,
)
from demiurge.storage import EventLog, VersionStore
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
        principal_scope: PrincipalScope | None = None,
        initialize_session: bool = True,
    ):
        self.home = home
        self.version_store = version_store
        self.core_loader = core_loader
        self.provider = provider
        self.tool_runtime = tool_runtime
        self.core_id = core_id
        self.session_id = session_id or utc_id("session_")
        self.principal_scope = principal_scope
        self._turn_session_ids: dict[str, str] = {}
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
        self.turn_engine = turn_engine or TurnEngine(RunnerTurnEngineHost(self))
        self.interaction_router = interaction_router or SessionInteractionRouter()
        self.prepare_live_core_callback = prepare_live_core
        self.context_assembler = ContextAssembler()
        self.event_log = EventLog(home, self.session_id)
        self.prompt_context = PromptContextRuntime(
            assembler=self.context_assembler,
            sessions=self.sessions,
            interaction_router=self.interaction_router,
            show_system_prompt=lambda: self.show_system_prompt,
            emit_event=self.emit_turn_event,
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
        self.session_routes = SessionRoutingRuntime(
            sessions=self.sessions,
            session_id=lambda: self.session_id,
            activate_session=self._activate_session,
            runtime_timezone=self.runtime_timezone,
            emit_event=lambda event_type, **payload: self.event_log.emit(event_type, **payload),
        )
        self.history: list[LLMMessage] = []
        self.display_turns: list[dict[str, Any]] = []
        self._session_started_ids: set[str] = set()
        self.background_tasks = BackgroundWorkRuntime(self.task_worker)
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
            work_lifecycle=self.task_worker.host_work,
        )
        self.module_delivery = ModuleDeliveryRuntime(RunnerModuleDeliveryHost(self))
        self.interaction_dispatch = InteractionDispatchRuntime(
            delivery_runtime=self.delivery_runtime,
            track_background_task=self.background_tasks.track,
        )
        self.slot_effects = SlotEffectRuntime(
            home=self.home,
            workspace=self.workspace,
            module_delivery=self.module_delivery,
            dispatch=self.interaction_dispatch,
            on_history_changed=self._refresh_history,
        )
        self.slot_context = SlotContextRuntime(RunnerSlotContextHost(self), effects=self.slot_effects)
        self.slot_pipeline = SlotPipelineRuntime(
            slot_runtime=self.slot_runtime,
            slot_context=self.slot_context,
            slot_effects=self.slot_effects,
            emit_event=self.emit_slot_event,
            track_background_task=self.background_tasks.track,
            refresh_history=self._refresh_history,
        )
        self.runtime_io = TurnIO(RunnerTurnIOHost(self))
        self.turn_execution = TurnExecution(
            RunnerTurnPipelineHost(self),
            admission=TurnAdmissionRuntime(RunnerTurnAdmissionHost(self)),
            persistence=TurnPersistenceRuntime(RunnerTurnPersistenceHost(self)),
            scope_resolver=PrincipalScopeResolver(self.session_runtime.store),
        )
        if initialize_session:
            self._ensure_current_session()

    def emit_turn_event(self, event_type: str, **payload: Any) -> dict[str, Any]:
        session_id = self._event_session_id(payload)
        payload.pop("session_id", None)
        return EventLog(self.home, session_id).emit(event_type, **payload)

    def emit_slot_event(self, event_type: str, **payload: Any) -> dict[str, Any]:
        return self.emit_turn_event(event_type, **payload)

    def _event_session_id(self, payload: Mapping[str, Any]) -> str:
        turn_id = payload.get("turn_id")
        if turn_id and str(turn_id) in self._turn_session_ids:
            return self._turn_session_ids[str(turn_id)]
        explicit = payload.get("session_id")
        if explicit:
            return str(explicit)
        return self.session_id

    def release_turn_event_scope(self, turn_id: str) -> None:
        self._turn_session_ids.pop(turn_id, None)

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
        return await self.turn_execution.run(
            TurnRequest(
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
        session_id: str,
        turn_id: str,
        step_id: str,
        use_bootstrap_context: bool = True,
    ) -> list[LLMMessage]:
        return self.prompt_context.build_messages(
            PromptBuildRequest(
                session_id=session_id,
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

    def _session_core_binding(self, core: LoadedCore) -> SessionCoreBinding:
        return SessionCoreBinding(
            core_id=core.core_id,
            core_revision=self._core_revision(core),
            provider=self.provider_name,
            model=self._resolve_model_name(core),
            workspace=self.workspace,
        )

    def start_new_session(
        self,
        *,
        channel: str | None = None,
        conversation_key: str | None = None,
        principal_key: str | None = None,
        source: str | None = None,
        reply_to: str | None = None,
        replace_conversation_binding: bool = False,
    ) -> str:
        previous_session_id = self.session_id
        core = self.core_loader.load(self.version_store.active_core_path(self.core_id))
        new_scope: PrincipalScope | None = None
        resolver = PrincipalScopeResolver(self.session_runtime.store)
        if channel and conversation_key and channel != "tui":
            new_session_id = utc_id("session_")
            new_scope = resolver.issue_conversation(
                channel=channel,
                principal_key=principal_key or conversation_key,
                conversation_key=conversation_key,
                session_id=new_session_id,
            )
        elif self.principal_scope is not None and self.principal_scope.authority is AuthorityKind.OPERATOR:
            new_session_id = utc_id("session_")
            new_scope = resolver.local_operator(
                active_session_id=new_session_id,
                reason="start new local operator session",
                allow_unowned_active=True,
            )
        record = self.session_routes.start_new(
            self._session_core_binding(core),
            channel=channel,
            conversation_key=conversation_key,
            source=source,
            reply_to=reply_to,
            replace_conversation_binding=replace_conversation_binding,
            principal_scope=new_scope,
        )
        if new_scope is not None and new_scope.authority is AuthorityKind.OPERATOR:
            self.principal_scope = new_scope
        if record.session_id != previous_session_id:
            self.tool_runtime.approval_runtime.invalidate_session(
                previous_session_id
            )
        return record.session_id

    def resume_session(
        self,
        session_id: str,
        *,
        channel: str | None = None,
        conversation_key: str | None = None,
        principal_key: str | None = None,
        source: str | None = None,
        reply_to: str | None = None,
        replace_conversation_binding: bool = False,
    ) -> None:
        resolver = PrincipalScopeResolver(self.session_runtime.store)
        if self.principal_scope is not None and self.principal_scope.authority is AuthorityKind.OPERATOR:
            resume_scope = resolver.local_operator(
                active_session_id=self.session_id,
                reason=f"resume operator session {session_id}",
            )
        elif channel and conversation_key:
            resume_scope = resolver.conversation(
                channel=channel,
                principal_key=principal_key or conversation_key,
                conversation_key=conversation_key,
                session_id=session_id,
            )
        elif self.principal_scope is not None:
            resume_scope = self.principal_scope
        else:
            resume_scope = resolver.origin_scope(session_id=self.session_id)
        self.session_routes.resume(
            session_id,
            channel=channel,
            conversation_key=conversation_key,
            source=source,
            reply_to=reply_to,
            replace_conversation_binding=replace_conversation_binding,
            principal_scope=resume_scope,
        )

    async def compact_session(self, *, focus: str | None = None, protect_last_n: int = 6) -> CompactionResult:
        return await self.session_compaction.compact(focus=focus, protect_last_n=protect_last_n)

    def _ensure_current_session(self) -> None:
        core = self.core_loader.load(self.initial_core_path or self.version_store.active_core_path(self.core_id))
        self.session_routes.ensure_current(
            self._session_core_binding(core),
            principal_scope=self.principal_scope,
        )

    def _activate_session(self, session_id: str) -> None:
        if not self.sessions.exists(session_id):
            raise FileNotFoundError(f"session not found: {session_id}")
        self.session_id = session_id
        self._bind_event_log()
        self.history = self._session_history_messages()

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

    def _module_result_client(self, *, session_id: str, writable: bool) -> ModuleResultClient:
        return self.slot_context.result_client(session_id=session_id, writable=writable)

    async def execute_tool(
        self,
        call: ToolCall,
        *,
        core: LoadedCore,
        turn: TurnContext,
        capability: CapabilityFacade,
        execution_context: TurnExecutionContext | None = None,
        principal_scope: PrincipalScope | None = None,
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
            execution_context=execution_context,
            principal_scope=principal_scope,
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
        execution_context: TurnExecutionContext,
        output_factory: Callable[[SlotDefinition], Any],
    ) -> ToolResult:
        return await self.execute_tool(
            call,
            core=core,
            turn=turn,
            capability=capability,
            execution_context=execution_context,
            emit_event=lambda event_type, **payload: self.emit_turn_event(
                event_type,
                **{**payload, "session_id": turn.session_id},
            ),
            output_factory=output_factory,
        )

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
