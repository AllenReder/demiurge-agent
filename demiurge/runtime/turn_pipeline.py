from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from demiurge.core import LoadedCore
from demiurge.providers import LLMMessage
from demiurge.runtime.bootstrap import BootstrapSlotRequest
from demiurge.runtime.interactions import (
    InteractionDelivery,
    InteractionInbound,
    InteractionItem,
    SessionRouteBinding,
    SessionRouteToken,
)
from demiurge.runtime.scope import AuthorityKind, PrincipalScope, PrincipalScopeResolver
from demiurge.runtime.slot_context import ModuleResultClient, ModuleStateStores
from demiurge.runtime.slots import InputPipelineRequest, InputPipelineResult, OutputPipelineRequest, ResolvedPhaseSlots
from demiurge.runtime.turn import TurnEngineRequest, TurnEngineResult
from demiurge.runtime.turn_lifecycle import TurnLifecycle, TurnLifecycleCompletion, TurnLifecycleRequest
from demiurge.security.capabilities import CapabilityFacade, CapabilitySnapshot
from demiurge.sdk import AgentInput, InputEnvelope, TurnContext
from demiurge.tools.records import ToolExecutionRecord
from demiurge.tools.registry import ResolvedEffectCatalog


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
class TurnCancelResult:
    turn_id: str
    status: Literal["cancelled", "not_found"]


@dataclass(frozen=True, slots=True)
class TurnRequest:
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


@dataclass(frozen=True, slots=True)
class TurnCancellation:
    turn_id: str


@dataclass(frozen=True, slots=True)
class TurnAdmissionLease:
    lease_id: str
    session_id: str
    turn_id: str


@dataclass(slots=True)
class _TurnCancellationState:
    token: TurnCancellation
    task: asyncio.Task[Any] = field(repr=False)

    def cancel(self) -> bool:
        if self.task.done():
            return False
        return self.task.cancel()


@dataclass(frozen=True, slots=True)
class TurnExecutionContext:
    session_id: str
    principal_scope: PrincipalScope
    core_id: str
    core_revision: str
    capability_snapshot: CapabilitySnapshot
    workspace: str | None
    route_token: SessionRouteToken | None
    trace_id: str
    cancellation: TurnCancellation
    admission_lease: TurnAdmissionLease


@dataclass(slots=True)
class _AdmittedTurn:
    context: TurnExecutionContext
    core: LoadedCore
    capability: CapabilityFacade
    lifecycle: TurnLifecycle
    turn: TurnContext
    interaction_metadata: dict[str, Any]
    state_stores: ModuleStateStores
    input_envelope: InputEnvelope
    cancellation_state: _TurnCancellationState
    admission_lock: asyncio.Lock

    @property
    def session_id(self) -> str:
        return self.context.session_id

    @property
    def principal_scope(self) -> PrincipalScope:
        return self.context.principal_scope

    @property
    def core_revision(self) -> str:
        return self.context.core_revision

    @property
    def route_token(self) -> SessionRouteToken | None:
        return self.context.route_token


class TurnAdmissionHost(Protocol):
    @property
    def session_id(self) -> str:
        ...

    def is_session_started(self, session_id: str) -> bool:
        ...

    @property
    def workspace(self) -> str | None:
        ...

    async def load_core(self, core_path: Path | None) -> LoadedCore:
        ...

    def current_core_revision(self, core_path: Path | None) -> str | None:
        ...

    def interaction_metadata(self, interaction: InteractionInbound | None) -> dict[str, Any]:
        ...

    def resolve_session_for_interaction(
        self,
        core: LoadedCore,
        interaction: InteractionInbound | None,
        interaction_metadata: dict[str, Any],
    ) -> PrincipalScope:
        ...

    def bind_route(
        self,
        route_binding: SessionRouteBinding,
        *,
        session_id: str,
    ) -> SessionRouteToken:
        ...

    def update_active_session_core(self, core: LoadedCore, *, session_id: str) -> None:
        ...

    def core_revision(self, core: LoadedCore) -> str:
        ...

    def emit_event(self, event_type: str, **payload: Any) -> dict[str, Any]:
        ...

    def mark_session_started(self, session_id: str) -> None:
        ...

    def begin_turn(self, request: TurnLifecycleRequest) -> TurnLifecycle:
        ...


class TurnPersistenceHost(Protocol):
    def emit_event(self, event_type: str, **payload: Any) -> dict[str, Any]:
        ...

    def interrupt_turn(self, lifecycle: TurnLifecycle, *, status: str, error: str) -> None:
        ...

    def send_user_message(
        self,
        *,
        session_id: str,
        turn_id: str,
        content: str,
        interaction_metadata: dict[str, Any],
    ) -> None:
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


class TurnPipelineHost(Protocol):
    def bind_principal_scope(self, scope: _AdmittedTurn) -> None:
        ...

    def release_principal_scope(self, scope: _AdmittedTurn) -> None:
        ...

    def activate_execution_route(self, token: SessionRouteToken | None) -> object | None:
        ...

    def release_execution_route(self, handle: object | None) -> None:
        ...

    async def ensure_bootstrap(self, request: BootstrapSlotRequest) -> None:
        ...

    async def run_input_slots(self, request: InputPipelineRequest) -> InputPipelineResult:
        ...

    async def prepare_tools(
        self,
        core: LoadedCore,
        turn: TurnContext,
        *,
        capability: CapabilityFacade,
        execution_context: TurnExecutionContext,
    ) -> None:
        ...

    def effect_catalog_for(
        self,
        core: LoadedCore,
        turn: TurnContext,
    ) -> ResolvedEffectCatalog:
        ...

    async def run_turn_engine(self, request: TurnEngineRequest) -> TurnEngineResult:
        ...

    def result_client(self, *, session_id: str, writable: bool) -> ModuleResultClient:
        ...

    async def run_output_slots(self, request: OutputPipelineRequest) -> list[InteractionItem]:
        ...

    async def drain_turn_deliveries(self, turn_id: str) -> None:
        ...

    async def cancel_turn_deliveries(self, turn_id: str) -> None:
        ...


class RunnerTurnAdmissionHost:
    """Adapter from SessionTurnStepRunner to TurnAdmissionHost."""

    def __init__(self, runner: Any):
        self.runner = runner

    @property
    def session_id(self) -> str:
        return self.runner.session_id

    def is_session_started(self, session_id: str) -> bool:
        return session_id in self.runner._session_started_ids

    @property
    def workspace(self) -> str | None:
        return self.runner.workspace

    async def load_core(self, core_path: Path | None) -> LoadedCore:
        if core_path is not None:
            return self.runner.core_loader.load(core_path)
        return await self.runner.load_active_core()

    def current_core_revision(self, core_path: Path | None) -> str | None:
        if core_path is not None:
            return None
        try:
            return self.runner.version_store.active_pointer(
                self.runner.core_id
            ).active_revision
        except Exception:
            return "untracked"

    def interaction_metadata(self, interaction: InteractionInbound | None) -> dict[str, Any]:
        return self.runner.session_routes.metadata_for(interaction)

    def resolve_session_for_interaction(
        self,
        core: LoadedCore,
        interaction: InteractionInbound | None,
        interaction_metadata: dict[str, Any],
    ) -> PrincipalScope:
        return self.runner.session_routes.resolve_for_interaction(
            self.runner._session_core_binding(core),
            interaction,
            interaction_metadata,
            fixed_scope=self.runner.principal_scope,
        )

    def bind_route(
        self,
        route_binding: SessionRouteBinding,
        *,
        session_id: str,
    ) -> SessionRouteToken:
        return route_binding.bind(self.runner.interaction_router, session_id)

    def update_active_session_core(self, core: LoadedCore, *, session_id: str) -> None:
        self.runner.session_runtime.update_session(
            session_id,
            core_id=core.core_id,
            core_revision=self.runner._core_revision(core),
            provider=self.runner.provider_name,
            model=self.runner._resolve_model_name(core),
            touch=False,
        )

    def core_revision(self, core: LoadedCore) -> str:
        return self.runner._core_revision(core)

    def emit_event(self, event_type: str, **payload: Any) -> dict[str, Any]:
        return self.runner.emit_turn_event(event_type, **payload)

    def mark_session_started(self, session_id: str) -> None:
        self.runner._session_started_ids.add(session_id)

    def begin_turn(self, request: TurnLifecycleRequest) -> TurnLifecycle:
        lifecycle = self.runner.turn_lifecycle.begin(request)
        self.runner._turn_session_ids[lifecycle.turn_id] = lifecycle.session_id
        return lifecycle


class RunnerTurnPersistenceHost:
    """Adapter from SessionTurnStepRunner to TurnPersistenceHost."""

    def __init__(self, runner: Any):
        self.runner = runner

    def emit_event(self, event_type: str, **payload: Any) -> dict[str, Any]:
        return self.runner.emit_turn_event(event_type, **payload)

    def interrupt_turn(self, lifecycle: TurnLifecycle, *, status: str, error: str) -> None:
        try:
            self.runner.turn_lifecycle.interrupt(lifecycle, status=status, error=error)
        finally:
            self.runner.release_turn_event_scope(lifecycle.turn_id)

    def send_user_message(
        self,
        *,
        session_id: str,
        turn_id: str,
        content: str,
        interaction_metadata: dict[str, Any],
    ) -> None:
        self.runner.runtime_io.send_user(
            session_id=session_id,
            turn_id=turn_id,
            content=content,
            interaction_metadata=interaction_metadata,
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
        try:
            self.runner.turn_lifecycle.complete(lifecycle, completion)
        finally:
            self.runner.release_turn_event_scope(lifecycle.turn_id)

    def sanitize_runtime_error(self, exc: Exception) -> str:
        return self.runner._sanitize_runtime_error(exc)


class RunnerTurnPipelineHost:
    """Adapter from SessionTurnStepRunner to the authored turn pipeline."""

    def __init__(self, runner: Any):
        self.runner = runner

    def bind_principal_scope(self, scope: _AdmittedTurn) -> None:
        self.runner.task_worker.bind_turn_scope(
            session_id=scope.session_id,
            turn_id=scope.lifecycle.turn_id,
            scope=scope.principal_scope,
        )

    def release_principal_scope(self, scope: _AdmittedTurn) -> None:
        self.runner.task_worker.release_turn_scope(
            session_id=scope.session_id,
            turn_id=scope.lifecycle.turn_id,
        )

    def activate_execution_route(self, token: SessionRouteToken | None) -> object | None:
        return self.runner.interaction_router.activate_execution_route(token)

    def release_execution_route(self, handle: object | None) -> None:
        self.runner.interaction_router.release_execution_route(handle)

    async def ensure_bootstrap(self, request: BootstrapSlotRequest) -> None:
        await self.runner.bootstrap_slots.ensure(request)

    async def run_input_slots(self, request: InputPipelineRequest) -> InputPipelineResult:
        return await self.runner.slot_pipeline.run_input(request)

    async def prepare_tools(
        self,
        core: LoadedCore,
        turn: TurnContext,
        *,
        capability: CapabilityFacade,
        execution_context: TurnExecutionContext,
    ) -> None:
        await self.runner.tool_runtime.prepare_for_turn(
            core,
            turn,
            capability=capability,
            execution_context=execution_context,
            emit_event=lambda event_type, **payload: self.runner.emit_turn_event(
                event_type,
                **{**payload, "session_id": turn.session_id},
            ),
        )

    def effect_catalog_for(
        self,
        core: LoadedCore,
        turn: TurnContext,
    ) -> ResolvedEffectCatalog:
        return self.runner.tool_runtime.resolve_effects(core, turn=turn)

    async def run_turn_engine(self, request: TurnEngineRequest) -> TurnEngineResult:
        return await self.runner.turn_engine.run(request)

    def result_client(self, *, session_id: str, writable: bool) -> ModuleResultClient:
        return self.runner._module_result_client(session_id=session_id, writable=writable)

    async def run_output_slots(self, request: OutputPipelineRequest) -> list[InteractionItem]:
        return await self.runner.slot_pipeline.run_output(request)

    async def drain_turn_deliveries(self, turn_id: str) -> None:
        await self.runner.interaction_dispatch.drain_turn(turn_id)

    async def cancel_turn_deliveries(self, turn_id: str) -> None:
        await self.runner.interaction_dispatch.cancel_turn(turn_id)


class TurnAdmissionRuntime:
    """Admits a raw turn request into a resolved execution scope."""

    def __init__(self, host: TurnAdmissionHost):
        self.host = host
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._session_users: dict[str, int] = {}

    async def admit(self, request: TurnRequest) -> _AdmittedTurn:
        revision_before_load = self.host.current_core_revision(request.core_path)
        core = await self.host.load_core(request.core_path)
        initial_core_revision = self.host.core_revision(core)
        initial_snapshot_consistent = (
            revision_before_load is None
            or revision_before_load == initial_core_revision
        )
        interaction_metadata = self.host.interaction_metadata(request.interaction)
        principal_scope = self.host.resolve_session_for_interaction(
            core,
            request.interaction,
            interaction_metadata,
        )
        session_id = self.host.session_id
        if principal_scope.session_id != session_id:
            raise RuntimeError("PrincipalScope session does not match admitted session")
        admission_lock = self._session_locks.setdefault(session_id, asyncio.Lock())
        self._session_users[session_id] = self._session_users.get(session_id, 0) + 1
        acquired = False
        try:
            await admission_lock.acquire()
            acquired = True
            route_token = None
            if request.route_binding is not None:
                route_token = self.host.bind_route(
                    request.route_binding,
                    session_id=session_id,
                )
            core, core_revision = await self._pin_core_snapshot(
                core_path=request.core_path,
                initial_core=core,
                initial_revision=initial_core_revision,
                initial_snapshot_consistent=initial_snapshot_consistent,
            )
            if request.core_path is None:
                self.host.update_active_session_core(core, session_id=session_id)

            capability_snapshot = CapabilitySnapshot.capture(core)
            capability = CapabilityFacade(core, snapshot=capability_snapshot)
            if not self.host.is_session_started(session_id):
                self.host.emit_event(
                    "session.started",
                    **{
                        **interaction_metadata,
                        "session_id": session_id,
                        "core_id": core.core_id,
                        "core_revision": core_revision,
                    },
                )
                self.host.mark_session_started(session_id)
            lifecycle = self.host.begin_turn(
                TurnLifecycleRequest(
                    session_id=session_id,
                    core_id=core.core_id,
                    core_revision=core_revision,
                    raw_text=request.text,
                    metadata=interaction_metadata,
                    attachments=tuple(request.interaction.attachments) if request.interaction is not None else (),
                )
            )
            task = asyncio.current_task()
            if task is None:
                raise RuntimeError("TurnExecution admission requires an active asyncio task")
            cancellation = TurnCancellation(turn_id=lifecycle.turn_id)
            context = TurnExecutionContext(
                session_id=session_id,
                principal_scope=principal_scope,
                core_id=core.core_id,
                core_revision=core_revision,
                capability_snapshot=capability_snapshot,
                workspace=self.host.workspace,
                route_token=route_token,
                trace_id=lifecycle.turn_id,
                cancellation=cancellation,
                admission_lease=TurnAdmissionLease(
                    lease_id=f"admission:{lifecycle.turn_id}",
                    session_id=session_id,
                    turn_id=lifecycle.turn_id,
                ),
            )
            return _AdmittedTurn(
                context=context,
                core=core,
                capability=capability,
                lifecycle=lifecycle,
                turn=lifecycle.turn,
                interaction_metadata=interaction_metadata,
                state_stores=lifecycle.state_stores,
                input_envelope=lifecycle.input_envelope,
                cancellation_state=_TurnCancellationState(
                    token=cancellation,
                    task=task,
                ),
                admission_lock=admission_lock,
            )
        except BaseException:
            self._release_lock(
                session_id=session_id,
                admission_lock=admission_lock,
                acquired=acquired,
            )
            raise

    def release(self, scope: _AdmittedTurn) -> None:
        self._release_lock(
            session_id=scope.session_id,
            admission_lock=scope.admission_lock,
            acquired=True,
        )

    def _release_lock(
        self,
        *,
        session_id: str,
        admission_lock: asyncio.Lock,
        acquired: bool,
    ) -> None:
        if acquired and admission_lock.locked():
            admission_lock.release()
        users = self._session_users.get(session_id, 0) - 1
        if users > 0:
            self._session_users[session_id] = users
            return
        self._session_users.pop(session_id, None)
        if self._session_locks.get(session_id) is admission_lock:
            self._session_locks.pop(session_id, None)

    async def _pin_core_snapshot(
        self,
        *,
        core_path: Path | None,
        initial_core: LoadedCore,
        initial_revision: str,
        initial_snapshot_consistent: bool,
    ) -> tuple[LoadedCore, str]:
        if core_path is not None:
            return initial_core, initial_revision

        if (
            initial_snapshot_consistent
            and self.host.core_revision(initial_core) == initial_revision
        ):
            return initial_core, initial_revision

        previous = initial_core
        for _ in range(3):
            revision_before = self.host.core_revision(previous)
            core = await self.host.load_core(None)
            revision_after = self.host.core_revision(core)
            if revision_before == revision_after:
                return core, revision_after
            previous = core
        raise RuntimeError("active core changed repeatedly during turn admission")


class TurnPersistenceRuntime:
    """Persists foreground turn input, completion, and interruption records."""

    def __init__(self, host: TurnPersistenceHost):
        self.host = host

    def record_input(self, scope: _AdmittedTurn, input_result: InputPipelineResult) -> None:
        scope.turn.user_input = AgentInput(content=input_result.user_text, metadata=scope.interaction_metadata)
        self.host.emit_event(
            "message.received",
            turn_id=scope.lifecycle.turn_id,
            content=input_result.user_text,
            **scope.interaction_metadata,
        )
        if input_result.persisted_user_text:
            self.host.send_user_message(
                session_id=scope.session_id,
                turn_id=scope.lifecycle.turn_id,
                content=input_result.persisted_user_text,
                interaction_metadata=scope.interaction_metadata,
            )

    def interrupt_cancelled(self, scope: _AdmittedTurn) -> None:
        self.host.interrupt_turn(scope.lifecycle, status="cancelled", error="turn cancelled")

    def interrupt_failed(self, scope: _AdmittedTurn, exc: Exception) -> None:
        self.host.interrupt_turn(
            scope.lifecycle,
            status="failed",
            error=self.host.sanitize_runtime_error(exc),
        )

    def complete(
        self,
        scope: _AdmittedTurn,
        *,
        user_text: str,
        items: list[InteractionItem],
        turn_messages: list[LLMMessage],
        tool_records: list[ToolExecutionRecord],
        agent_result: Any,
        needs_user: bool,
    ) -> TurnResult:
        delivered_texts = [
            item.delivery.text
            for item in items
            if item.kind == "delivery" and item.delivery is not None and item.delivery.visible and item.delivery.text
        ]
        for text in delivered_texts:
            turn_messages.append(LLMMessage(role="assistant", content=text))

        self.host.refresh_history()
        self.host.append_display_turn(
            turn_id=scope.lifecycle.turn_id,
            user_text=user_text,
            delivered_texts=delivered_texts,
            tool_records=tool_records,
        )
        self.host.complete_turn(
            scope.lifecycle,
            TurnLifecycleCompletion(
                items=tuple(items),
                agent_result=agent_result,
                needs_user=needs_user,
                result_ref=scope.lifecycle.turn_id,
            ),
        )
        return TurnResult(
            session_id=scope.session_id,
            turn_id=scope.lifecycle.turn_id,
            core_id=scope.core.core_id,
            core_revision=scope.core_revision,
            items=items,
            agent_result=agent_result,
            needs_user=needs_user,
        )


class TurnExecution:
    """Own one admitted turn from Host scope resolution through completion."""

    def __init__(
        self,
        host: TurnPipelineHost,
        *,
        admission: TurnAdmissionRuntime,
        persistence: TurnPersistenceRuntime,
        scope_resolver: PrincipalScopeResolver,
    ):
        self.host = host
        self.admission = admission
        self.persistence = persistence
        self.scope_resolver = scope_resolver
        self._active_turns: dict[str, _AdmittedTurn] = {}

    async def run(self, request: TurnRequest) -> TurnResult:
        self._validate_request(request)
        scope = await self.admission.admit(request)
        self._active_turns[scope.lifecycle.turn_id] = scope
        scope_bound = False
        completed = False
        route_handle: object | None = None
        try:
            route_handle = self.host.activate_execution_route(scope.route_token)
            self.host.bind_principal_scope(scope)
            scope_bound = True
            result = await self._run_admitted(request, scope)
            completed = True
            return result
        except asyncio.CancelledError:
            self.persistence.interrupt_cancelled(scope)
            raise
        except Exception as exc:
            self.persistence.interrupt_failed(scope, exc)
            raise
        finally:
            try:
                if not completed:
                    await self.host.cancel_turn_deliveries(scope.lifecycle.turn_id)
            finally:
                if self._active_turns.get(scope.lifecycle.turn_id) is scope:
                    self._active_turns.pop(scope.lifecycle.turn_id, None)
                try:
                    if scope_bound:
                        self.host.release_principal_scope(scope)
                finally:
                    try:
                        self.host.release_execution_route(route_handle)
                    finally:
                        self.admission.release(scope)

    def cancel(
        self,
        turn_id: str,
        principal_scope: PrincipalScope,
    ) -> TurnCancelResult:
        self.scope_resolver.validate_owned(principal_scope)
        active = self._active_turns.get(turn_id)
        if active is None or not self._can_control(principal_scope, active):
            return TurnCancelResult(turn_id=turn_id, status="not_found")
        if not active.cancellation_state.cancel():
            return TurnCancelResult(turn_id=turn_id, status="not_found")
        return TurnCancelResult(turn_id=turn_id, status="cancelled")

    @staticmethod
    def _can_control(
        principal_scope: PrincipalScope,
        context: _AdmittedTurn,
    ) -> bool:
        if principal_scope.authority is AuthorityKind.OPERATOR:
            return True
        owner = context.principal_scope
        return (
            principal_scope.authority is owner.authority
            and principal_scope.principal_id == owner.principal_id
            and principal_scope.session_id == context.session_id
        )

    async def _run_admitted(self, request: TurnRequest, scope: _AdmittedTurn) -> TurnResult:
        turn = scope.turn

        if request.use_bootstrap:
            await self.host.ensure_bootstrap(
                BootstrapSlotRequest(
                    session_id=scope.session_id,
                    core=scope.core,
                    core_revision=scope.core_revision,
                    capability=scope.capability,
                    workspace=scope.context.workspace,
                    interaction_metadata=scope.interaction_metadata,
                )
            )

        input_result = await self.host.run_input_slots(
            InputPipelineRequest(
                core=scope.core,
                turn=turn,
                capability=scope.capability,
                envelope=scope.input_envelope,
                state_stores=scope.state_stores,
                interaction_metadata=scope.interaction_metadata,
                injected_system_context=request.injected_system_context or [],
                slot_ids=request.input_slot_ids,
                phase_slots=request.input_phase_slots,
            )
        )

        user_text = input_result.user_text
        context = input_result.context
        self.persistence.record_input(scope, input_result)
        items: list[InteractionItem] = list(input_result.items)
        await self.host.prepare_tools(
            scope.core,
            turn,
            capability=scope.capability,
            execution_context=scope.context,
        )
        effect_catalog = self.host.effect_catalog_for(scope.core, turn)
        available_tools = effect_catalog.definitions()

        engine_result = await self.host.run_turn_engine(
            TurnEngineRequest(
                core=scope.core,
                turn=turn,
                capability=scope.capability,
                execution_context=scope.context,
                context=context,
                available_tools=available_tools,
                effect_catalog=effect_catalog,
                interaction_metadata=scope.interaction_metadata,
                use_bootstrap_context=request.use_bootstrap,
            )
        )

        final_output = engine_result.final_output
        needs_user = engine_result.needs_user
        tool_records = engine_result.tool_records
        turn_messages = engine_result.turn_messages
        items.extend(engine_result.items)

        result_client = self.host.result_client(session_id=scope.session_id, writable=True)
        output_items = await self.host.run_output_slots(
            OutputPipelineRequest(
                core=scope.core,
                turn=turn,
                capability=scope.capability,
                current_output=final_output,
                tool_records=tool_records,
                state_stores=scope.state_stores,
                interaction_metadata=scope.interaction_metadata,
                result_client=result_client,
                slot_ids=request.output_slot_ids,
                phase_slots=request.output_phase_slots,
            )
        )

        items.extend(output_items)
        await self.host.drain_turn_deliveries(scope.lifecycle.turn_id)
        return self.persistence.complete(
            scope,
            user_text=user_text,
            items=items,
            turn_messages=turn_messages,
            tool_records=tool_records,
            agent_result=result_client.value,
            needs_user=needs_user,
        )

    @staticmethod
    def _validate_request(request: TurnRequest) -> None:
        if request.input_slot_ids is not None and request.input_phase_slots is not None:
            raise ValueError("input_slot_ids and input_phase_slots cannot both be set")
        if request.output_slot_ids is not None and request.output_phase_slots is not None:
            raise ValueError("output_slot_ids and output_phase_slots cannot both be set")
