from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

from demiurge.providers import LLMMessage
from demiurge.runtime.interactions import InteractionDelivery, InteractionItem
from demiurge.runtime.turn import TurnEngineRequest, TurnEngineResult
from demiurge.runtime.turn_lifecycle import TurnLifecycle, TurnLifecycleCompletion, TurnLifecycleRequest
from demiurge.runtime.turn_pipeline import TurnPipelineRequest, TurnPipelineRuntime
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


class _FakeTurnPipelineHost:
    def __init__(
        self,
        *,
        engine_result: TurnEngineResult | None = None,
        engine_error: Exception | None = None,
    ) -> None:
        self.session_id = "session_1"
        self.workspace = "/workspace"
        self.session_started = False
        self.core = _Core()
        self.engine_result = engine_result or TurnEngineResult(final_output="model answer")
        self.engine_error = engine_error
        self.events: list[dict[str, Any]] = []
        self.bootstrap_requests: list[Any] = []
        self.sent_users: list[dict[str, Any]] = []
        self.engine_requests: list[TurnEngineRequest] = []
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

    def bind_route(self, route_binding):
        self.bound_route = route_binding

    def update_active_session_core(self, core):
        self.updated_core = core.core_id

    def resolve_phase_slots(self, core, kind, slot_ids):
        return None

    def core_revision(self, core):
        return "rev_1"

    def emit_event(self, event_type: str, **payload: Any):
        event = {"type": event_type, **payload}
        self.events.append(event)
        return event

    def mark_session_started(self):
        self.session_started = True

    async def ensure_bootstrap(self, request):
        self.bootstrap_requests.append(request)

    def begin_turn(self, request: TurnLifecycleRequest):
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
            task_id="turn_1",
            input_envelope=InputEnvelope(raw_text=request.raw_text, metadata=dict(request.metadata)),
            user_input=user_input,
            turn=turn,
            state_stores=object(),
            metadata=dict(request.metadata),
        )

    def interrupt_turn(self, lifecycle, *, status: str, error: str):
        self.interrupts.append({"turn_id": lifecycle.turn_id, "status": status, "error": error})

    async def run_input_slots(
        self,
        core,
        turn,
        capability,
        lifecycle,
        *,
        interaction_metadata,
        injected_system_context,
        serial_slots,
        phase_slots,
    ):
        context = [ContextContribution(type="instruction", content=item) for item in injected_system_context]
        return "normalized hello", "persisted hello", context, [InteractionItem(kind="input_slot")]

    def send_user_message(self, *, turn_id: str, content: str, interaction_metadata: dict[str, Any]):
        self.sent_users.append({"turn_id": turn_id, "content": content, "metadata": dict(interaction_metadata)})

    async def prepare_tools(self, core, turn):
        self.prepared_tools_for = turn.turn_id

    def tool_definitions_for(self, core, turn):
        return []

    async def run_turn_engine(self, request: TurnEngineRequest):
        self.engine_requests.append(request)
        if self.engine_error is not None:
            raise self.engine_error
        return self.engine_result

    def result_client(self, *, writable: bool):
        return SimpleNamespace(value={"ok": True})

    async def run_output_slots(
        self,
        core,
        turn,
        capability,
        *,
        current_output,
        tool_records,
        lifecycle,
        interaction_metadata,
        result_client,
        serial_slots,
        phase_slots,
    ):
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


@pytest.mark.asyncio
async def test_turn_pipeline_runs_full_host_lifecycle():
    host = _FakeTurnPipelineHost(
        engine_result=TurnEngineResult(
            final_output="model answer",
            turn_messages=[LLMMessage(role="assistant", content="model answer")],
        )
    )

    result = await TurnPipelineRuntime(host).run(
        TurnPipelineRequest(text="hello", injected_system_context=["extra context"])
    )

    assert result.session_id == "session_1"
    assert result.turn_id == "turn_1"
    assert result.core_revision == "rev_1"
    assert result.agent_result == {"ok": True}
    assert [event["type"] for event in host.events] == ["session.started", "message.received"]
    assert host.session_started is True
    assert host.bootstrap_requests[0].workspace == "/workspace"
    assert host.sent_users == [{"turn_id": "turn_1", "content": "persisted hello", "metadata": {"timezone": "UTC"}}]
    assert host.engine_requests[0].context[0].content == "extra context"
    assert host.completed is not None
    assert host.completed.agent_result == {"ok": True}
    assert host.history_refreshed is True
    assert host.display_turns == [
        {"turn_id": "turn_1", "user": "normalized hello", "assistant": ["visible output"], "tools": []}
    ]


@pytest.mark.asyncio
async def test_turn_pipeline_interrupts_failed_engine_turn():
    host = _FakeTurnPipelineHost(engine_error=RuntimeError("boom\nnoisy"))

    with pytest.raises(RuntimeError, match="boom"):
        await TurnPipelineRuntime(host).run(TurnPipelineRequest(text="hello"))

    assert host.completed is None
    assert host.interrupts == [
        {"turn_id": "turn_1", "status": "failed", "error": "sanitized: RuntimeError: boom noisy"}
    ]
