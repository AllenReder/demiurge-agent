from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

from demiurge.providers import LLMMessage
from demiurge.runtime.interactions import InteractionDelivery, InteractionInbound, InteractionItem, SessionRouteBinding
from demiurge.runtime.slots import InputPipelineResult
from demiurge.runtime.turn import TurnEngineRequest, TurnEngineResult
from demiurge.runtime.turn_lifecycle import TurnLifecycle, TurnLifecycleCompletion, TurnLifecycleRequest
from demiurge.runtime.turn_pipeline import (
    TurnAdmissionRuntime,
    TurnPersistenceRuntime,
    TurnPipelineRequest,
    TurnPipelineRuntime,
)
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
    ) -> None:
        self.session_id = "session_1"
        self.workspace = "/workspace"
        self.session_started = False
        self.core = _Core()
        self.engine_result = engine_result or TurnEngineResult(final_output="model answer")
        self.engine_error = engine_error
        self.prepare_error = prepare_error
        self.events: list[dict[str, Any]] = []
        self.bootstrap_requests: list[Any] = []
        self.sent_users: list[dict[str, Any]] = []
        self.input_requests: list[Any] = []
        self.output_requests: list[Any] = []
        self.result_client_session_ids: list[str | None] = []
        self.engine_requests: list[TurnEngineRequest] = []
        self.begin_requests: list[TurnLifecycleRequest] = []
        self.completed: TurnLifecycleCompletion | None = None
        self.interrupts: list[dict[str, str]] = []
        self.display_turns: list[dict[str, Any]] = []
        self.history_refreshed = False

    async def load_core(self, core_path):
        return self.core

    def interaction_metadata(self, interaction):
        return {"channel": interaction.channel} if interaction is not None else {"timezone": "UTC"}

    def resolve_session_for_interaction(self, core, interaction_metadata):
        self.resolved_session = (core.core_id, dict(interaction_metadata))

    def bind_route(self, route_binding, *, session_id: str):
        self.bound_route = route_binding

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
        self.bootstrap_requests.append(request)

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
        self.prepared_tools_for = turn.turn_id

    def tool_definitions_for(self, core, turn):
        return []

    async def run_turn_engine(self, request: TurnEngineRequest):
        self.engine_requests.append(request)
        if self.engine_error is not None:
            raise self.engine_error
        return self.engine_result

    def result_client(self, *, writable: bool, session_id: str | None = None):
        self.result_client_session_ids.append(session_id)
        return SimpleNamespace(value={"ok": True})

    async def run_output_slots(self, request):
        self.output_requests.append(request)
        return [InteractionItem.delivery_item(InteractionDelivery(text="visible output"))]

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


def _runtime(host: _FakeTurnRuntimeHost) -> TurnPipelineRuntime:
    return TurnPipelineRuntime(
        host,
        admission=TurnAdmissionRuntime(host),
        persistence=TurnPersistenceRuntime(host),
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
    scope = await admission.admit(
        TurnPipelineRequest(text="hello", interaction=inbound, route_binding=route_binding)
    )

    assert scope.session_id == "session_1"
    assert scope.core is host.core
    assert scope.core_revision == "rev_1"
    assert scope.interaction_metadata == {"channel": "telegram"}
    assert scope.input_envelope.raw_text == "hello"
    assert scope.input_envelope.attachments == ["image"]
    assert host.resolved_session == ("assistant", {"channel": "telegram"})
    assert host.bound_route is route_binding
    assert host.updated_core == "assistant"
    assert [event["type"] for event in host.events] == ["session.started"]
    assert host.session_started is True
    assert host.bootstrap_requests[0].session_id == "session_1"
    assert host.bootstrap_requests[0].workspace == "/workspace"
    assert host.begin_requests[0].attachments == ("image",)
    admission.release(scope)


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
async def test_turn_pipeline_runs_full_host_lifecycle():
    host = _FakeTurnRuntimeHost(
        engine_result=TurnEngineResult(
            final_output="model answer",
            turn_messages=[LLMMessage(role="assistant", content="model answer")],
        )
    )

    result = await _runtime(host).run(TurnPipelineRequest(text="hello", injected_system_context=["extra context"]))

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
    assert host.completed.agent_result == {"ok": True}
    assert host.history_refreshed is True
    assert host.display_turns == [
        {"turn_id": "turn_1", "user": "normalized hello", "assistant": ["visible output"], "tools": []}
    ]


@pytest.mark.asyncio
async def test_turn_pipeline_interrupts_failed_engine_turn():
    host = _FakeTurnRuntimeHost(engine_error=RuntimeError("boom\nnoisy"))
    runtime = _runtime(host)

    with pytest.raises(RuntimeError, match="boom"):
        await runtime.run(TurnPipelineRequest(text="hello"))

    assert host.completed is None
    assert host.interrupts == [
        {"turn_id": "turn_1", "status": "failed", "error": "sanitized: RuntimeError: boom noisy"}
    ]

    host.engine_error = None
    recovered = await runtime.run(TurnPipelineRequest(text="retry"))
    assert recovered.session_id == "session_1"


@pytest.mark.asyncio
async def test_turn_pipeline_interrupts_prepare_failure_and_releases_admission():
    host = _FakeTurnRuntimeHost(prepare_error=RuntimeError("prepare failed"))
    runtime = _runtime(host)

    with pytest.raises(RuntimeError, match="prepare failed"):
        await runtime.run(TurnPipelineRequest(text="hello"))

    assert host.interrupts == [
        {
            "turn_id": "turn_1",
            "status": "failed",
            "error": "sanitized: RuntimeError: prepare failed",
        }
    ]

    host.prepare_error = None
    recovered = await runtime.run(TurnPipelineRequest(text="retry"))
    assert recovered.session_id == "session_1"
