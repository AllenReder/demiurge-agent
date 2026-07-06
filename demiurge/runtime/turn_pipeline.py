from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from demiurge.core import LoadedCore, SlotDefinition
from demiurge.providers import LLMMessage
from demiurge.runtime.bootstrap import BootstrapSlotRequest
from demiurge.runtime.child_agents import ResolvedPhaseSlots
from demiurge.runtime.interactions import InteractionDelivery, InteractionInbound, InteractionItem, SessionRouteBinding
from demiurge.runtime.slot_context import ModuleResultClient
from demiurge.runtime.turn import TurnEngineRequest, TurnEngineResult
from demiurge.runtime.turn_lifecycle import TurnLifecycle, TurnLifecycleCompletion, TurnLifecycleRequest
from demiurge.security.capabilities import CapabilityFacade
from demiurge.sdk import AgentInput, ContextContribution, TurnContext
from demiurge.tools.records import ToolExecutionRecord


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


@dataclass(frozen=True, slots=True)
class TurnPipelineRequest:
    text: str
    core_path: Path | None = None
    interaction: InteractionInbound | None = None
    injected_system_context: list[str] | None = None
    input_slot_ids: list[str] | tuple[str, ...] | None = None
    output_slot_ids: list[str] | tuple[str, ...] | None = None
    input_phase_slots: ResolvedPhaseSlots | None = None
    output_phase_slots: ResolvedPhaseSlots | None = None
    use_bootstrap: bool = True
    route_binding: SessionRouteBinding | None = None


class TurnPipelineHost(Protocol):
    @property
    def session_id(self) -> str:
        ...

    @property
    def session_started(self) -> bool:
        ...

    @property
    def workspace(self) -> str | None:
        ...

    async def load_core(self, core_path: Path | None) -> LoadedCore:
        ...

    def interaction_metadata(self, interaction: InteractionInbound | None) -> dict[str, Any]:
        ...

    def resolve_session_for_interaction(self, core: LoadedCore, interaction_metadata: dict[str, Any]) -> None:
        ...

    def bind_route(self, route_binding: SessionRouteBinding) -> None:
        ...

    def update_active_session_core(self, core: LoadedCore) -> None:
        ...

    def resolve_phase_slots(
        self,
        core: LoadedCore,
        kind: str,
        slot_ids: list[str] | tuple[str, ...] | None,
    ) -> list[SlotDefinition] | None:
        ...

    def core_revision(self, core: LoadedCore) -> str:
        ...

    def emit_event(self, event_type: str, **payload: Any) -> dict[str, Any]:
        ...

    def mark_session_started(self) -> None:
        ...

    async def ensure_bootstrap(self, request: BootstrapSlotRequest) -> None:
        ...

    def begin_turn(self, request: TurnLifecycleRequest) -> TurnLifecycle:
        ...

    def interrupt_turn(self, lifecycle: TurnLifecycle, *, status: str, error: str) -> None:
        ...

    async def run_input_slots(
        self,
        core: LoadedCore,
        turn: TurnContext,
        capability: CapabilityFacade,
        lifecycle: TurnLifecycle,
        *,
        interaction_metadata: dict[str, Any],
        injected_system_context: list[str],
        serial_slots: list[SlotDefinition] | None,
        phase_slots: ResolvedPhaseSlots | None,
    ) -> tuple[str, str, list[ContextContribution], list[InteractionItem]]:
        ...

    def send_user_message(self, *, turn_id: str, content: str, interaction_metadata: dict[str, Any]) -> None:
        ...

    async def prepare_tools(self, core: LoadedCore, turn: TurnContext) -> None:
        ...

    def tool_definitions_for(self, core: LoadedCore, turn: TurnContext) -> list[Any]:
        ...

    async def run_turn_engine(self, request: TurnEngineRequest) -> TurnEngineResult:
        ...

    def result_client(self, *, writable: bool) -> ModuleResultClient:
        ...

    async def run_output_slots(
        self,
        core: LoadedCore,
        turn: TurnContext,
        capability: CapabilityFacade,
        *,
        current_output: str,
        tool_records: list[ToolExecutionRecord],
        lifecycle: TurnLifecycle,
        interaction_metadata: dict[str, Any],
        result_client: ModuleResultClient,
        serial_slots: list[SlotDefinition] | None,
        phase_slots: ResolvedPhaseSlots | None,
    ) -> list[InteractionItem]:
        ...

    def refresh_history(self) -> None:
        ...

    def append_display_turn(
        self,
        *,
        turn_id: str,
        user_text: str,
        delivered_texts: list[str],
        tool_records: list[ToolExecutionRecord],
    ) -> None:
        ...

    def complete_turn(self, lifecycle: TurnLifecycle, completion: TurnLifecycleCompletion) -> None:
        ...

    def sanitize_runtime_error(self, exc: Exception) -> str:
        ...


class RunnerTurnPipelineHost:
    """Adapter from SessionTurnStepRunner to TurnPipelineHost."""

    def __init__(self, runner: Any):
        self.runner = runner

    @property
    def session_id(self) -> str:
        return self.runner.session_id

    @property
    def session_started(self) -> bool:
        return self.runner._session_started

    @property
    def workspace(self) -> str | None:
        return self.runner.workspace

    async def load_core(self, core_path: Path | None) -> LoadedCore:
        if core_path is not None:
            return self.runner.core_loader.load(core_path)
        return await self.runner.load_active_core()

    def interaction_metadata(self, interaction: InteractionInbound | None) -> dict[str, Any]:
        return self.runner._interaction_metadata(interaction)

    def resolve_session_for_interaction(self, core: LoadedCore, interaction_metadata: dict[str, Any]) -> None:
        self.runner._resolve_session_for_interaction(core, interaction_metadata)

    def bind_route(self, route_binding: SessionRouteBinding) -> None:
        route_binding.bind(self.runner.interaction_router, self.runner.session_id)

    def update_active_session_core(self, core: LoadedCore) -> None:
        self.runner.session_runtime.update_session(
            self.runner.session_id,
            core_id=core.core_id,
            core_revision=self.runner._core_revision(core),
            provider=self.runner.provider_name,
            model=self.runner._resolve_model_name(core),
            touch=False,
        )

    def resolve_phase_slots(
        self,
        core: LoadedCore,
        kind: str,
        slot_ids: list[str] | tuple[str, ...] | None,
    ) -> list[SlotDefinition] | None:
        return self.runner._resolve_phase_slots(core, kind, slot_ids)

    def core_revision(self, core: LoadedCore) -> str:
        return self.runner._core_revision(core)

    def emit_event(self, event_type: str, **payload: Any) -> dict[str, Any]:
        return self.runner.event_log.emit(event_type, **payload)

    def mark_session_started(self) -> None:
        self.runner._session_started_ids.add(self.runner.session_id)

    async def ensure_bootstrap(self, request: BootstrapSlotRequest) -> None:
        await self.runner.bootstrap_slots.ensure(request)

    def begin_turn(self, request: TurnLifecycleRequest) -> TurnLifecycle:
        return self.runner.turn_lifecycle.begin(request)

    def interrupt_turn(self, lifecycle: TurnLifecycle, *, status: str, error: str) -> None:
        self.runner.turn_lifecycle.interrupt(lifecycle, status=status, error=error)

    async def run_input_slots(
        self,
        core: LoadedCore,
        turn: TurnContext,
        capability: CapabilityFacade,
        lifecycle: TurnLifecycle,
        *,
        interaction_metadata: dict[str, Any],
        injected_system_context: list[str],
        serial_slots: list[SlotDefinition] | None,
        phase_slots: ResolvedPhaseSlots | None,
    ) -> tuple[str, str, list[ContextContribution], list[InteractionItem]]:
        return await self.runner._run_input_slots(
            core,
            turn,
            capability,
            lifecycle.input_envelope,
            lifecycle.state_stores,
            interaction_metadata=interaction_metadata,
            injected_system_context=injected_system_context,
            serial_slots=serial_slots,
            phase_slots=phase_slots,
        )

    def send_user_message(self, *, turn_id: str, content: str, interaction_metadata: dict[str, Any]) -> None:
        self.runner.runtime_io.send_user(
            turn_id=turn_id,
            content=content,
            interaction_metadata=interaction_metadata,
        )

    async def prepare_tools(self, core: LoadedCore, turn: TurnContext) -> None:
        await self.runner.tool_runtime.prepare_for_turn(core, turn, emit_event=self.runner.event_log.emit)

    def tool_definitions_for(self, core: LoadedCore, turn: TurnContext) -> list[Any]:
        return self.runner.tool_runtime.definitions_for(core, turn=turn)

    async def run_turn_engine(self, request: TurnEngineRequest) -> TurnEngineResult:
        return await self.runner.turn_engine.run(request)

    def result_client(self, *, writable: bool) -> ModuleResultClient:
        return self.runner._module_result_client(writable=writable)

    async def run_output_slots(
        self,
        core: LoadedCore,
        turn: TurnContext,
        capability: CapabilityFacade,
        *,
        current_output: str,
        tool_records: list[ToolExecutionRecord],
        lifecycle: TurnLifecycle,
        interaction_metadata: dict[str, Any],
        result_client: ModuleResultClient,
        serial_slots: list[SlotDefinition] | None,
        phase_slots: ResolvedPhaseSlots | None,
    ) -> list[InteractionItem]:
        return await self.runner._run_output_slots(
            core,
            turn,
            capability,
            current_output=current_output,
            tool_records=tool_records,
            state_stores=lifecycle.state_stores,
            interaction_metadata=interaction_metadata,
            result_client=result_client,
            serial_slots=serial_slots,
            phase_slots=phase_slots,
        )

    def refresh_history(self) -> None:
        self.runner._refresh_history()

    def append_display_turn(
        self,
        *,
        turn_id: str,
        user_text: str,
        delivered_texts: list[str],
        tool_records: list[ToolExecutionRecord],
    ) -> None:
        self.runner.display_turns.append(
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

    def complete_turn(self, lifecycle: TurnLifecycle, completion: TurnLifecycleCompletion) -> None:
        self.runner.turn_lifecycle.complete(lifecycle, completion)

    def sanitize_runtime_error(self, exc: Exception) -> str:
        return self.runner._sanitize_runtime_error(exc)


class TurnPipelineRuntime:
    """Runs one authored Agent Core turn across input slots, model steps, output slots, and lifecycle."""

    def __init__(self, host: TurnPipelineHost):
        self.host = host

    async def run(self, request: TurnPipelineRequest) -> TurnResult:
        core = await self.host.load_core(request.core_path)
        interaction_metadata = self.host.interaction_metadata(request.interaction)
        self.host.resolve_session_for_interaction(core, interaction_metadata)
        if request.route_binding is not None:
            self.host.bind_route(request.route_binding)
        if request.core_path is None:
            self.host.update_active_session_core(core)
        if request.input_slot_ids is not None and request.input_phase_slots is not None:
            raise ValueError("input_slot_ids and input_phase_slots cannot both be set")
        if request.output_slot_ids is not None and request.output_phase_slots is not None:
            raise ValueError("output_slot_ids and output_phase_slots cannot both be set")

        input_slots_override = self.host.resolve_phase_slots(core, "input", request.input_slot_ids)
        output_slots_override = self.host.resolve_phase_slots(core, "output", request.output_slot_ids)
        core_revision = self.host.core_revision(core)
        capability = CapabilityFacade(core)
        if not self.host.session_started:
            self.host.emit_event(
                "session.started",
                core_id=core.core_id,
                core_revision=core_revision,
                **interaction_metadata,
            )
            self.host.mark_session_started()
        if request.use_bootstrap:
            await self.host.ensure_bootstrap(
                BootstrapSlotRequest(
                    session_id=self.host.session_id,
                    core=core,
                    core_revision=core_revision,
                    capability=capability,
                    workspace=self.host.workspace,
                    interaction_metadata=interaction_metadata,
                )
            )

        lifecycle = self.host.begin_turn(
            TurnLifecycleRequest(
                session_id=self.host.session_id,
                core_id=core.core_id,
                core_revision=core_revision,
                raw_text=request.text,
                metadata=interaction_metadata,
                attachments=tuple(request.interaction.attachments) if request.interaction is not None else (),
            )
        )
        turn_id = lifecycle.turn_id
        turn = lifecycle.turn

        try:
            user_text, persisted_user_text, context, input_items = await self.host.run_input_slots(
                core,
                turn,
                capability,
                lifecycle,
                interaction_metadata=interaction_metadata,
                injected_system_context=request.injected_system_context or [],
                serial_slots=input_slots_override,
                phase_slots=request.input_phase_slots,
            )
        except asyncio.CancelledError:
            self.host.interrupt_turn(lifecycle, status="cancelled", error="turn cancelled")
            raise
        except Exception as exc:
            self.host.interrupt_turn(lifecycle, status="failed", error=self.host.sanitize_runtime_error(exc))
            raise

        turn.user_input = AgentInput(content=user_text, metadata=interaction_metadata)
        self.host.emit_event("message.received", turn_id=turn_id, content=user_text, **interaction_metadata)
        if persisted_user_text:
            self.host.send_user_message(
                turn_id=turn_id,
                content=persisted_user_text,
                interaction_metadata=interaction_metadata,
            )
        items: list[InteractionItem] = list(input_items)
        await self.host.prepare_tools(core, turn)
        available_tools = self.host.tool_definitions_for(core, turn)

        try:
            engine_result = await self.host.run_turn_engine(
                TurnEngineRequest(
                    core=core,
                    turn=turn,
                    capability=capability,
                    context=context,
                    available_tools=available_tools,
                    interaction_metadata=interaction_metadata,
                    use_bootstrap_context=request.use_bootstrap,
                )
            )
        except asyncio.CancelledError:
            self.host.interrupt_turn(lifecycle, status="cancelled", error="turn cancelled")
            raise
        except Exception as exc:
            self.host.interrupt_turn(lifecycle, status="failed", error=self.host.sanitize_runtime_error(exc))
            raise

        final_output = engine_result.final_output
        needs_user = engine_result.needs_user
        tool_records = engine_result.tool_records
        turn_messages = engine_result.turn_messages
        items.extend(engine_result.items)

        result_client = self.host.result_client(writable=True)
        try:
            output_items = await self.host.run_output_slots(
                core,
                turn,
                capability,
                current_output=final_output,
                tool_records=tool_records,
                lifecycle=lifecycle,
                interaction_metadata=interaction_metadata,
                result_client=result_client,
                serial_slots=output_slots_override,
                phase_slots=request.output_phase_slots,
            )
        except asyncio.CancelledError:
            self.host.interrupt_turn(lifecycle, status="cancelled", error="turn cancelled")
            raise
        except Exception as exc:
            self.host.interrupt_turn(lifecycle, status="failed", error=self.host.sanitize_runtime_error(exc))
            raise

        items.extend(output_items)
        delivered_texts = [
            item.delivery.text
            for item in items
            if item.kind == "delivery" and item.delivery is not None and item.delivery.visible and item.delivery.text
        ]
        for text in delivered_texts:
            turn_messages.append(LLMMessage(role="assistant", content=text))

        self.host.refresh_history()
        self.host.append_display_turn(
            turn_id=turn_id,
            user_text=user_text,
            delivered_texts=delivered_texts,
            tool_records=tool_records,
        )
        self.host.complete_turn(
            lifecycle,
            TurnLifecycleCompletion(
                items=tuple(items),
                agent_result=result_client.value,
                needs_user=needs_user,
                result_ref=turn_id,
            ),
        )
        return TurnResult(
            session_id=self.host.session_id,
            turn_id=turn_id,
            core_id=core.core_id,
            core_revision=core_revision,
            items=items,
            agent_result=result_client.value,
            needs_user=needs_user,
        )
