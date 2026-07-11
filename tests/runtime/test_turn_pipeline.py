from __future__ import annotations

import asyncio
import gc
import tempfile
import weakref
from dataclasses import FrozenInstanceError, dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from demiurge.providers import LLMMessage
from demiurge.runtime.interactions import (
    InteractionDelivery,
    InteractionInbound,
    InteractionItem,
    SessionRouteBinding,
    SessionRouteToken,
)
from demiurge.runtime.control import RuntimeControlPlane
from demiurge.runtime.scope import AuthorityKind, PrincipalScopeResolver
from demiurge.runtime.session import SessionRuntime
from demiurge.runtime.store import RuntimeStore
from demiurge.runtime.tasks import RuntimeTaskWorker
from demiurge.runtime.slots import InputPipelineResult
from demiurge.runtime.turn import TurnEngineRequest, TurnEngineResult
from demiurge.runtime.turn_lifecycle import TurnLifecycle, TurnLifecycleCompletion, TurnLifecycleRequest
from demiurge.runtime.turn_pipeline import (
    TurnAdmissionLease,
    TurnAdmissionRuntime,
    TurnCancellation,
    TurnExecution,
    TurnExecutionContext,
    TurnPersistenceRuntime,
    TurnRequest,
)
from tests.runtime.operator_authority_support import activate_test_operator_authority
from demiurge.sdk import AgentInput, ContextContribution, InputEnvelope, TurnContext


@dataclass(slots=True)
class _RuntimeConfig:
    max_model_steps: int = 3


@dataclass(slots=True)
class _ModelConfig:
    model_name: str = "fake/model"


@dataclass(slots=True)
class _Manifest:
    runtime: _RuntimeConfig = field(default_factory=_RuntimeConfig)
    model: _ModelConfig = field(default_factory=_ModelConfig)


@dataclass(slots=True)
class _Core:
    core_id: str = "assistant"
    manifest: _Manifest = field(default_factory=_Manifest)
    raw_manifest: dict[str, Any] = field(default_factory=lambda: {"capabilities": {"defaults": {}}})
    bootstrap_slots: list[Any] = field(default_factory=list)
    input_slots: list[Any] = field(default_factory=list)
    output_slots: list[Any] = field(default_factory=list)
    tool_slots: list[Any] = field(default_factory=list)


class _FakeTurnRuntimeHost:
    def __init__(
        self,
        *,
        engine_result: TurnEngineResult | None = None,
        engine_error: Exception | None = None,
        prepare_error: Exception | None = None,
        bootstrap_error: Exception | None = None,
    ) -> None:
        self.session_id = "session_1"
        self.workspace = "/workspace"
        self.session_started = False
        self.core = _Core()
        self.engine_result = engine_result or TurnEngineResult(final_output="model answer")
        self.engine_error = engine_error
        self.prepare_error = prepare_error
        self.bootstrap_error = bootstrap_error
        self.events: list[dict[str, Any]] = []
        self.bootstrap_requests: list[Any] = []
        self.sent_users: list[dict[str, Any]] = []
        self.input_requests: list[Any] = []
        self.output_requests: list[Any] = []
        self.result_client_session_ids: list[str | None] = []
        self.engine_requests: list[TurnEngineRequest] = []
        self.bound_contexts: list[TurnExecutionContext] = []
        self.bound_admission_locks: list[weakref.ReferenceType[asyncio.Lock]] = []
        self.begin_requests: list[TurnLifecycleRequest] = []
        self.completed: TurnLifecycleCompletion | None = None
        self.interrupts: list[dict[str, str]] = []
        self.display_turns: list[dict[str, Any]] = []
        self.history_refreshed = False
        self._scope_home = tempfile.TemporaryDirectory()
        self.scope_resolver = PrincipalScopeResolver(
            RuntimeStore(Path(self._scope_home.name) / "runtime.sqlite3")
        )
        activate_test_operator_authority(self.scope_resolver.store)
        bootstrap_scope = self.scope_resolver.local_operator(
            active_session_id=self.session_id,
            reason="bootstrap test turn host",
            allow_unowned_active=True,
        )
        SessionRuntime(
            control_plane=RuntimeControlPlane(self.scope_resolver.store)
        ).create_session(
            session_id=self.session_id,
            core_id="assistant",
            core_revision="rev_1",
            principal_scope=bootstrap_scope,
        )
        self.task_worker = RuntimeTaskWorker(
            control_plane=RuntimeControlPlane(self.scope_resolver.store)
        )
        self.start_background_task = False
        self.background_task_id: str | None = None
        self.block_engine = False
        self.engine_started = asyncio.Event()
        self.release_engine = asyncio.Event()
        self.route_activation_error: Exception | None = None
        self.active_route_token: SessionRouteToken | None = None
        self.bootstrap_route_tokens: list[SessionRouteToken | None] = []
        self.cancelled_delivery_turns: list[str] = []
        self.block_stage: str | None = None
        self.stage_started = asyncio.Event()
        self.release_stage = asyncio.Event()

    async def load_core(self, core_path):
        return self.core

    def current_core_revision(self, core_path):
        return None if core_path is not None else "rev_1"

    def bind_principal_scope(self, scope):
        self.bound_contexts.append(scope.context)
        self.bound_admission_locks.append(weakref.ref(scope.admission_lock))
        self.task_worker.bind_turn_scope(
            session_id=scope.session_id,
            turn_id=scope.lifecycle.turn_id,
            scope=scope.principal_scope,
        )

    def release_principal_scope(self, scope):
        self.task_worker.release_turn_scope(
            session_id=scope.session_id,
            turn_id=scope.lifecycle.turn_id,
        )

    def activate_execution_route(self, token):
        if self.route_activation_error is not None:
            raise self.route_activation_error
        if token is None:
            return None
        previous = self.active_route_token
        self.active_route_token = token
        return (token, previous)

    def release_execution_route(self, handle):
        if handle is not None:
            _, previous = handle
            self.active_route_token = previous

    def interaction_metadata(self, interaction):
        return {"channel": interaction.channel} if interaction is not None else {"timezone": "UTC"}

    def resolve_session_for_interaction(self, core, interaction, interaction_metadata):
        self.resolved_session = (core.core_id, dict(interaction_metadata))
        if interaction is None:
            return self.scope_resolver.local_operator(
                active_session_id=self.session_id,
                reason="test turn admission operator",
                allow_unowned_active=True,
            )
        return self.scope_resolver.issue_conversation(
            channel=interaction.channel,
            principal_key=interaction.principal_key or interaction.source,
            conversation_key=interaction.conversation_key or f"{interaction.channel}:source:{interaction.source}",
            session_id=self.session_id,
        )

    def bind_route(self, route_binding, *, session_id: str):
        self.bound_route = route_binding
        route_binding.token = SessionRouteToken(
            token_id="route_test",
            session_id=session_id,
        )
        return route_binding.token

    def update_active_session_core(self, core, *, session_id: str):
        self.updated_core = core.core_id

    def core_revision(self, core):
        return "rev_1"

    def emit_event(self, event_type: str, **payload: Any):
        event = {"type": event_type, **payload}
        self.events.append(event)
        return event

    def is_session_started(self, session_id: str) -> bool:
        return self.session_started

    def mark_session_started(self, session_id: str):
        self.session_started = True

    async def ensure_bootstrap(self, request):
        self.bootstrap_route_tokens.append(self.active_route_token)
        self.bootstrap_requests.append(request)
        if self.bootstrap_error is not None:
            raise self.bootstrap_error

    def begin_turn(self, request: TurnLifecycleRequest):
        self.begin_requests.append(request)
        user_input = AgentInput(content=request.raw_text, metadata=dict(request.metadata))
        turn = TurnContext(
            session_id=request.session_id,
            turn_id="turn_1",
            core_id=request.core_id,
            core_revision=request.core_revision,
            user_input=user_input,
            metadata=dict(request.metadata),
        )
        return TurnLifecycle(
            session_id=request.session_id,
            turn_id="turn_1",
            input_envelope=InputEnvelope(
                raw_text=request.raw_text,
                metadata=dict(request.metadata),
                attachments=list(request.attachments),
            ),
            user_input=user_input,
            turn=turn,
            state_stores=object(),
            metadata=dict(request.metadata),
        )

    def interrupt_turn(self, lifecycle, *, status: str, error: str):
        self.interrupts.append({"turn_id": lifecycle.turn_id, "status": status, "error": error})

    async def run_input_slots(self, request):
        self.input_requests.append(request)
        await self._maybe_block_stage("input_slot")
        context = [ContextContribution(type="instruction", content=item) for item in request.injected_system_context]
        return InputPipelineResult(
            user_text="normalized hello",
            persisted_user_text="persisted hello",
            context=context,
            items=[InteractionItem(kind="input_slot")],
        )

    def send_user_message(
        self,
        *,
        session_id: str | None = None,
        turn_id: str,
        content: str,
        interaction_metadata: dict[str, Any],
    ):
        self.sent_users.append(
            {
                "session_id": session_id,
                "turn_id": turn_id,
                "content": content,
                "metadata": dict(interaction_metadata),
            }
        )

    async def prepare_tools(self, core, turn):
        if self.prepare_error is not None:
            raise self.prepare_error
        await self._maybe_block_stage("tool")
        self.prepared_tools_for = turn.turn_id

    def tool_definitions_for(self, core, turn):
        return []

    async def run_turn_engine(self, request: TurnEngineRequest):
        self.engine_requests.append(request)
        if self.engine_error is not None:
            raise self.engine_error
        await self._maybe_block_stage("provider")
        if self.block_engine:
            self.engine_started.set()
            await self.release_engine.wait()
        if self.start_background_task:
            async def task(ctx):
                return "done"

            record = self.task_worker.start_task(
                kind="terminal.exec",
                owner_session_id=request.turn.session_id,
                owner_turn_id=request.turn.turn_id,
                source_tool="test",
                task_factory=task,
            )
            self.background_task_id = record.task_id
        return self.engine_result

    def result_client(self, *, writable: bool, session_id: str | None = None):
        self.result_client_session_ids.append(session_id)
        return SimpleNamespace(value={"ok": True})

    async def run_output_slots(self, request):
        self.output_requests.append(request)
        await self._maybe_block_stage("output_slot")
        return [InteractionItem.delivery_item(InteractionDelivery(text="visible output"))]

    async def drain_turn_deliveries(self, turn_id: str):
        return None

    async def cancel_turn_deliveries(self, turn_id: str):
        self.cancelled_delivery_turns.append(turn_id)

    async def _maybe_block_stage(self, stage: str) -> None:
        if self.block_stage != stage:
            return
        self.stage_started.set()
        await self.release_stage.wait()

    def refresh_history(self):
        self.history_refreshed = True

    def append_display_turn(self, *, turn_id: str, user_text: str, delivered_texts: list[str], tool_records: list[Any]):
        self.display_turns.append(
            {
                "turn_id": turn_id,
                "user": user_text,
                "assistant": list(delivered_texts),
                "tools": list(tool_records),
            }
        )

    def complete_turn(self, lifecycle, completion: TurnLifecycleCompletion):
        self.completed = completion

    def sanitize_runtime_error(self, exc: Exception) -> str:
        return f"sanitized: {exc.__class__.__name__}: {str(exc).replace(chr(10), ' ')}"


def _runtime(host: _FakeTurnRuntimeHost) -> TurnExecution:
    return TurnExecution(
        host,
        admission=TurnAdmissionRuntime(host),
        persistence=TurnPersistenceRuntime(host),
        scope_resolver=host.scope_resolver,
    )


@pytest.mark.asyncio
async def test_turn_admission_runtime_resolves_session_bootstrap_and_begin_scope():
    host = _FakeTurnRuntimeHost()
    route_binding = SessionRouteBinding(route=SimpleNamespace())
    inbound = InteractionInbound(
        channel="telegram",
        text="hello",
        source="123",
        attachments=["image"],
    )

    admission = TurnAdmissionRuntime(host)
    admitted = await admission.admit(
        TurnRequest(text="hello", interaction=inbound, route_binding=route_binding)
    )
    context = admitted.context

    assert isinstance(context, TurnExecutionContext)
    assert context.session_id == "session_1"
    assert context.principal_scope.authority is AuthorityKind.CONVERSATION
    assert context.core_id == "assistant"
    assert context.core_revision == "rev_1"
    assert context.workspace == "/workspace"
    assert admitted.core is host.core
    assert admitted.interaction_metadata == {"channel": "telegram"}
    assert admitted.input_envelope.raw_text == "hello"
    assert admitted.input_envelope.attachments == ["image"]
    assert host.resolved_session == ("assistant", {"channel": "telegram"})
    assert host.bound_route is route_binding
    assert context.route_token == SessionRouteToken(
        token_id="route_test",
        session_id="session_1",
    )
    assert context.trace_id == "turn_1"
    assert isinstance(context.cancellation, TurnCancellation)
    assert context.cancellation.turn_id == "turn_1"
    assert not hasattr(context.cancellation, "cancel")
    assert isinstance(context.admission_lease, TurnAdmissionLease)
    assert context.admission_lease.session_id == "session_1"
    assert context.admission_lease.turn_id == "turn_1"
    assert not hasattr(context.admission_lease, "release")
    assert host.updated_core == "assistant"
    assert [event["type"] for event in host.events] == ["session.started"]
    assert host.session_started is True
    assert host.bootstrap_requests == []
    assert host.bootstrap_route_tokens == []
    assert host.active_route_token is None
    assert host.begin_requests[0].attachments == ("image",)
    with pytest.raises(FrozenInstanceError):
        context.core_revision = "rev_2"
    admission.release(admitted)


def test_turn_persistence_runtime_records_input_and_completes_turn():
    host = _FakeTurnRuntimeHost()
    lifecycle = host.begin_turn(
        TurnLifecycleRequest(
            session_id="session_1",
            core_id="assistant",
            core_revision="rev_1",
            raw_text="hello",
            metadata={"channel": "tui"},
        )
    )
    scope = SimpleNamespace(
        session_id="session_1",
        core=host.core,
        core_revision="rev_1",
        lifecycle=lifecycle,
        turn=lifecycle.turn,
        interaction_metadata={"channel": "tui"},
    )
    input_result = InputPipelineResult(
        user_text="normalized hello",
        persisted_user_text="persisted hello",
        context=[],
        items=[],
    )
    turn_messages = [LLMMessage(role="assistant", content="model answer")]
    item = InteractionItem.delivery_item(InteractionDelivery(text="visible output"))
    persistence = TurnPersistenceRuntime(host)

    persistence.record_input(scope, input_result)
    result = persistence.complete(
        scope,
        user_text=input_result.user_text,
        items=[item],
        turn_messages=turn_messages,
        tool_records=[],
        agent_result={"ok": True},
        needs_user=False,
    )

    assert scope.turn.user_input.content == "normalized hello"
    assert host.events == [{"type": "message.received", "turn_id": "turn_1", "content": "normalized hello", "channel": "tui"}]
    assert host.sent_users == [
        {
            "session_id": "session_1",
            "turn_id": "turn_1",
            "content": "persisted hello",
            "metadata": {"channel": "tui"},
        }
    ]
    assert turn_messages[-1] == LLMMessage(role="assistant", content="visible output")
    assert host.display_turns == [
        {"turn_id": "turn_1", "user": "normalized hello", "assistant": ["visible output"], "tools": []}
    ]
    assert host.completed is not None
    assert host.completed.agent_result == {"ok": True}
    assert result.agent_result == {"ok": True}
    assert result.items == [item]


@pytest.mark.asyncio
async def test_turn_execution_runs_full_host_lifecycle():
    host = _FakeTurnRuntimeHost(
        engine_result=TurnEngineResult(
            final_output="model answer",
            turn_messages=[LLMMessage(role="assistant", content="model answer")],
        )
    )

    result = await _runtime(host).run(TurnRequest(text="hello", injected_system_context=["extra context"]))

    assert result.session_id == "session_1"
    assert result.turn_id == "turn_1"
    assert result.core_revision == "rev_1"
    assert result.agent_result == {"ok": True}
    assert [event["type"] for event in host.events] == ["session.started", "message.received"]
    assert host.session_started is True
    assert host.bootstrap_requests[0].workspace == "/workspace"
    assert host.input_requests[0].envelope.raw_text == "hello"
    assert host.input_requests[0].state_stores is not None
    assert host.sent_users == [
        {
            "session_id": "session_1",
            "turn_id": "turn_1",
            "content": "persisted hello",
            "metadata": {"timezone": "UTC"},
        }
    ]
    assert host.engine_requests[0].context[0].content == "extra context"
    assert host.output_requests[0].current_output == "model answer"
    assert host.result_client_session_ids == ["session_1"]
    assert host.completed is not None


@pytest.mark.asyncio
async def test_turn_execution_keeps_admitted_session_core_and_route_immutable():
    host = _FakeTurnRuntimeHost()
    host.core.raw_manifest = {
        "capabilities": {"defaults": {"cap.admitted": True}},
    }
    admitted_core = host.core
    host.block_engine = True
    execution = _runtime(host)
    route_binding = SessionRouteBinding(route=SimpleNamespace())
    running = asyncio.create_task(
        execution.run(
            TurnRequest(
                text="hello",
                route_binding=route_binding,
            )
        )
    )
    await host.engine_started.wait()

    admitted = host.bound_contexts[0]
    assert host.bootstrap_requests[0].session_id == "session_1"
    assert host.bootstrap_requests[0].workspace == "/workspace"
    assert host.bootstrap_route_tokens == [
        SessionRouteToken(token_id="route_test", session_id="session_1")
    ]
    assert host.active_route_token == SessionRouteToken(
        token_id="route_test",
        session_id="session_1",
    )
    host.session_id = "session_2"
    host.core = _Core(core_id="replacement")
    admitted_core.raw_manifest["capabilities"]["defaults"] = {
        "cap.rebound": True,
    }
    route_binding.token = SessionRouteToken(
        token_id="route_rebound",
        session_id="session_2",
    )
    host.release_engine.set()

    result = await running

    assert result.session_id == "session_1"
    assert result.core_id == "assistant"
    assert result.core_revision == "rev_1"
    assert admitted.session_id == "session_1"
    assert admitted.core_id == "assistant"
    assert admitted.capability_snapshot.defaults == frozenset({"cap.admitted"})
    assert admitted.route_token == SessionRouteToken(
        token_id="route_test",
        session_id="session_1",
    )
    assert host.output_requests[0].core.core_id == "assistant"
    assert host.active_route_token is None


@pytest.mark.asyncio
async def test_turn_execution_captures_admitted_scope_for_background_completion():
    host = _FakeTurnRuntimeHost()
    host.start_background_task = True

    await _runtime(host).run(TurnRequest(text="hello"))
    assert host.background_task_id is not None
    await host.task_worker.wait(host.background_task_id, timeout_seconds=1)
    event = next(
        item
        for item in host.task_worker.pending_events_for_session(host.session_id)
        if item.task_id == host.background_task_id
    )

    assert event.origin_scope_record is not None
    assert event.origin_scope_record["authority"] == "operator"
    assert event.origin_scope_record["session_id"] == host.session_id
    assert event.origin_scope_record["allowed_session_ids"] == [host.session_id]
    assert host.task_worker.scope_for_turn(
        session_id=host.session_id,
        turn_id="turn_1",
    ) is None
    assert host.completed.agent_result == {"ok": True}
    assert host.history_refreshed is True
    assert host.display_turns == [
        {"turn_id": "turn_1", "user": "normalized hello", "assistant": ["visible output"], "tools": []}
    ]


@pytest.mark.asyncio
async def test_turn_execution_cancel_requires_owner_and_releases_admission():
    host = _FakeTurnRuntimeHost()
    host.block_engine = True
    execution = _runtime(host)
    running = asyncio.create_task(execution.run(TurnRequest(text="hello")))
    await host.engine_started.wait()

    outsider_candidate = host.scope_resolver.issue_conversation(
        channel="slack",
        principal_key="outsider",
        conversation_key="slack:channel:T1:outsider",
        session_id="session_2",
    )
    SessionRuntime(
        control_plane=RuntimeControlPlane(host.scope_resolver.store)
    ).create_session(
        session_id="session_2",
        core_id="assistant",
        core_revision="rev_1",
        principal_scope=outsider_candidate,
    )
    outsider = host.scope_resolver.conversation(
        channel="slack",
        principal_key="outsider",
        conversation_key="slack:channel:T1:outsider",
        session_id="session_2",
    )

    try:
        denied = execution.cancel("turn_1", outsider)
        assert denied.status == "not_found"
        assert running.done() is False

        operator = host.scope_resolver.local_operator(
            active_session_id="session_1",
            reason="cancel owned running turn",
        )
        cancelled = execution.cancel("turn_1", operator)
        assert cancelled.status == "cancelled"
        with pytest.raises(asyncio.CancelledError):
            await running
    finally:
        if not running.done():
            running.cancel()
            with pytest.raises(asyncio.CancelledError):
                await running

    assert host.interrupts == [
        {
            "turn_id": "turn_1",
            "status": "cancelled",
            "error": "turn cancelled",
        }
    ]
    host.block_engine = False
    recovered = await execution.run(TurnRequest(text="retry"))
    assert recovered.session_id == "session_1"


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["input_slot", "tool", "provider", "output_slot"])
async def test_turn_execution_cancel_releases_admission_at_each_runtime_stage(stage):
    host = _FakeTurnRuntimeHost()
    host.block_stage = stage
    execution = _runtime(host)
    running = asyncio.create_task(execution.run(TurnRequest(text="hello")))
    await asyncio.wait_for(host.stage_started.wait(), timeout=1)
    operator = host.scope_resolver.local_operator(
        active_session_id="session_1",
        reason=f"cancel during {stage}",
    )

    cancelled = execution.cancel("turn_1", operator)

    assert cancelled.status == "cancelled"
    with pytest.raises(asyncio.CancelledError):
        await running
    assert host.cancelled_delivery_turns == ["turn_1"]
    host.block_stage = None
    recovered = await asyncio.wait_for(
        execution.run(TurnRequest(text="retry")),
        timeout=1,
    )
    assert recovered.session_id == "session_1"


@pytest.mark.asyncio
async def test_turn_execution_interrupts_failed_engine_turn():
    host = _FakeTurnRuntimeHost(engine_error=RuntimeError("boom\nnoisy"))
    runtime = _runtime(host)

    with pytest.raises(RuntimeError, match="boom"):
        await runtime.run(TurnRequest(text="hello"))

    assert host.completed is None
    assert host.interrupts == [
        {"turn_id": "turn_1", "status": "failed", "error": "sanitized: RuntimeError: boom noisy"}
    ]
    assert host.task_worker.scope_for_turn(session_id="session_1", turn_id="turn_1") is None

    host.engine_error = None
    recovered = await runtime.run(TurnRequest(text="retry"))
    assert recovered.session_id == "session_1"


@pytest.mark.asyncio
async def test_turn_execution_interrupts_prepare_failure_and_releases_admission():
    host = _FakeTurnRuntimeHost(prepare_error=RuntimeError("prepare failed"))
    runtime = _runtime(host)

    with pytest.raises(RuntimeError, match="prepare failed"):
        await runtime.run(TurnRequest(text="hello"))

    assert host.interrupts == [
        {
            "turn_id": "turn_1",
            "status": "failed",
            "error": "sanitized: RuntimeError: prepare failed",
        }
    ]
    assert host.task_worker.scope_for_turn(session_id="session_1", turn_id="turn_1") is None

    host.prepare_error = None
    recovered = await runtime.run(TurnRequest(text="retry"))
    assert recovered.session_id == "session_1"


@pytest.mark.asyncio
async def test_turn_execution_interrupts_bootstrap_failure_and_releases_admission():
    host = _FakeTurnRuntimeHost(bootstrap_error=RuntimeError("bootstrap failed"))
    runtime = _runtime(host)

    with pytest.raises(RuntimeError, match="bootstrap failed"):
        await runtime.run(TurnRequest(text="hello"))

    assert host.interrupts == [
        {
            "turn_id": "turn_1",
            "status": "failed",
            "error": "sanitized: RuntimeError: bootstrap failed",
        }
    ]
    host.bootstrap_error = None
    recovered = await runtime.run(TurnRequest(text="retry"))
    assert recovered.session_id == "session_1"


@pytest.mark.asyncio
async def test_turn_execution_releases_admission_when_route_activation_fails():
    host = _FakeTurnRuntimeHost()
    runtime = _runtime(host)
    host.route_activation_error = RuntimeError("route activation failed")

    with pytest.raises(RuntimeError, match="route activation failed"):
        await runtime.run(TurnRequest(text="hello"))

    host.route_activation_error = None
    recovered = await asyncio.wait_for(
        runtime.run(TurnRequest(text="retry")),
        timeout=1,
    )
    assert recovered.session_id == "session_1"


@pytest.mark.asyncio
async def test_turn_execution_releases_idle_session_admission_resources():
    host = _FakeTurnRuntimeHost()
    runtime = _runtime(host)

    await runtime.run(TurnRequest(text="hello"))
    admission_lock = host.bound_admission_locks[0]
    host.bound_contexts.clear()
    gc.collect()

    assert admission_lock() is None
