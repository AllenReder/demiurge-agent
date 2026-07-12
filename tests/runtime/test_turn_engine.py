from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from demiurge.providers import LLMMessage, LLMRequest, LLMResponse, ToolCall, ToolDefinition
from demiurge.runtime.interactions import InteractionItem, ToolInteractionRecord
from demiurge.runtime.store import RuntimeEvent
from demiurge.runtime.turn import TurnEngine, TurnEngineRequest
from demiurge.sdk import AgentInput, ContextContribution, ToolResult, TurnContext
from demiurge.tools.registry import (
    EffectRequest,
    EffectResult,
    ResolvedEffectCatalog,
    ResolvedEffectEntry,
)
from demiurge.tools.records import ToolExecutionRecord


@dataclass(slots=True)
class _RuntimeConfig:
    max_model_steps: int = 3


@dataclass(slots=True)
class _Manifest:
    runtime: _RuntimeConfig = field(default_factory=_RuntimeConfig)


@dataclass(slots=True)
class _Core:
    manifest: _Manifest = field(default_factory=_Manifest)


class _FakeTurnHost:
    def __init__(
        self,
        responses: list[LLMResponse],
        *,
        tool_results: dict[str, ToolResult] | None = None,
    ):
        self.responses = list(responses)
        self.tool_results = dict(tool_results or {})
        self.events: list[dict[str, Any]] = []
        self.runtime_events: list[RuntimeEvent] = []
        self.provider_requests: list[LLMRequest] = []
        self.effect_requests: list[EffectRequest] = []
        self.debug_messages: list[list[LLMMessage]] = []

    def emit_event(self, event_type: str, **payload: Any) -> dict[str, Any]:
        event = {"type": event_type, **payload}
        self.events.append(event)
        return event

    def build_messages(
        self,
        core,
        context: list[ContextContribution],
        turn_messages: list[LLMMessage],
        *,
        session_id: str,
        turn_id: str,
        step_id: str,
        use_bootstrap_context: bool,
    ) -> list[LLMMessage]:
        messages = [LLMMessage(role="system", content=item.content or "") for item in context]
        messages.extend(turn_messages)
        return messages

    async def deliver_system_prompt_debug(
        self,
        messages: list[LLMMessage],
        *,
        turn: TurnContext,
        step_id: str,
        interaction_metadata: dict[str, Any],
    ) -> None:
        self.debug_messages.append(messages)

    def resolve_model_name(self, core) -> str:
        return "fake-model"

    async def complete_provider(self, request: LLMRequest) -> LLMResponse:
        self.provider_requests.append(request)
        return self.responses.pop(0)

    async def send_assistant_step(
        self,
        *,
        turn: TurnContext,
        step_id: str,
        content: str,
        tool_calls: list[ToolCall],
        interaction_metadata: dict[str, Any],
    ):
        return None, [InteractionItem(kind="assistant_step", metadata={"step_id": step_id, "content": content})]

    async def send_tool_call_started(
        self,
        *,
        turn: TurnContext,
        step_id: str,
        call: ToolCall,
        interaction_metadata: dict[str, Any],
    ) -> InteractionItem:
        return InteractionItem.tool_call_item(ToolInteractionRecord.started(call), metadata={"step_id": step_id})

    async def execute_tool(
        self,
        request: EffectRequest,
        *,
        core,
        turn: TurnContext,
        capability,
        execution_context,
        output_factory,
    ) -> EffectResult:
        self.effect_requests.append(request)
        return EffectResult.normalize(
            request.entry,
            self.tool_results[request.name],
        )

    def output_client(
        self,
        slot,
        *,
        turn: TurnContext,
        capability,
        interaction_metadata: dict[str, Any],
        items: list[InteractionItem],
    ) -> object:
        return object()

    async def send_tool_call_finished(
        self,
        *,
        turn: TurnContext,
        step_id: str,
        record: ToolExecutionRecord,
        interaction_metadata: dict[str, Any],
    ) -> InteractionItem:
        return InteractionItem.tool_result_item(record, metadata={"step_id": step_id})

    def append_runtime_event(self, event: RuntimeEvent) -> None:
        self.runtime_events.append(event)

    def tool_result_model_content(self, result: ToolResult) -> str:
        return result.model_output or result.content

    def truncate_model_content(self, content: str) -> str:
        return content[:80]


class _ResolvedRequestHost(_FakeTurnHost):
    def __init__(self, responses: list[LLMResponse], *, tool_results: dict[str, ToolResult]):
        super().__init__(responses, tool_results=tool_results)
        self.effect_requests: list[EffectRequest] = []

    async def execute_tool(
        self,
        request: EffectRequest,
        *,
        core,
        turn: TurnContext,
        capability,
        execution_context,
        output_factory,
    ) -> EffectResult:
        self.effect_requests.append(request)
        return EffectResult.normalize(
            request.entry,
            self.tool_results[request.name],
        )


def _request(*, context: list[ContextContribution] | None = None) -> TurnEngineRequest:
    entry = ResolvedEffectEntry(
        name="lookup",
        description="Lookup",
        input_schema={"type": "object"},
        source="authored",
        core_id="assistant",
        core_revision="rev_1",
        adapter_key="authored:agent/tools/lookup",
        provenance="authored:agent/tools/lookup",
        _adapter=object(),
    )
    catalog = ResolvedEffectCatalog(
        core_id="assistant",
        core_revision="rev_1",
        entries=(entry,),
    )
    return TurnEngineRequest(
        core=_Core(),
        turn=TurnContext(
            session_id="session_1",
            turn_id="turn_1",
            core_id="assistant",
            core_revision="rev_1",
            user_input=AgentInput(content="hello"),
            metadata={},
        ),
        capability=object(),
        execution_context=object(),
        context=context or [],
        available_tools=catalog.definitions(),
        effect_catalog=catalog,
        interaction_metadata={"channel": "test"},
    )


@pytest.mark.asyncio
async def test_turn_engine_runs_direct_final_response_through_host_interface():
    host = _FakeTurnHost([LLMResponse(content="final answer")])

    result = await TurnEngine(host).run(_request(context=[ContextContribution(type="instruction", content="ctx")]))

    assert result.final_output == "final answer"
    assert result.turn_messages[-1].content == "final answer"
    assert host.provider_requests[0].model == "fake-model"
    assert [event["type"] for event in host.events] == ["step.started", "message.completed"]
    assert host.debug_messages[0][0].content == "ctx"


@pytest.mark.asyncio
async def test_turn_engine_feeds_tool_result_back_to_provider():
    call = ToolCall(id="call_1", name="lookup", arguments={"q": "demo"})
    host = _FakeTurnHost(
        [
            LLMResponse(content="checking", tool_calls=[call]),
            LLMResponse(content="done"),
        ],
        tool_results={"lookup": ToolResult(content="tool data", model_output="model-visible data")},
    )

    result = await TurnEngine(host).run(_request())

    assert result.final_output == "done"
    assert [record.call.name for record in result.tool_records] == ["lookup"]
    assert len(host.effect_requests) == 1
    assert host.effect_requests[0].name == call.name
    assert host.effect_requests[0].call_id == call.id
    assert dict(host.effect_requests[0].arguments) == call.arguments
    assert len(host.provider_requests) == 2
    second_messages = host.provider_requests[1].messages
    assert any(message.role == "tool" and message.content == "model-visible data" for message in second_messages)
    assert [event.type for event in host.runtime_events] == ["tool.call.started", "tool.call.completed"]
    assert [event.payload["step_id"] for event in host.runtime_events] == ["turn_1_step_1", "turn_1_step_1"]
    assert host.runtime_events[-1].payload["effect_status"] == "succeeded"
    assert host.runtime_events[-1].payload["effect_error"] is None


@pytest.mark.asyncio
async def test_turn_engine_executes_the_same_resolved_entry_used_for_definitions():
    call = ToolCall(id="call_1", name="lookup", arguments={"q": "demo"})
    entry = ResolvedEffectEntry(
        name="lookup",
        description="Lookup",
        input_schema={"type": "object"},
        source="authored",
        core_id="assistant",
        core_revision="rev_1",
        adapter_key="authored:agent/tools/lookup",
        provenance="authored:agent/tools/lookup",
        _adapter=object(),
    )
    catalog = ResolvedEffectCatalog(
        core_id="assistant",
        core_revision="rev_1",
        entries=(entry,),
    )
    host = _ResolvedRequestHost(
        [LLMResponse(tool_calls=[call]), LLMResponse(content="done")],
        tool_results={"lookup": ToolResult(content="tool data")},
    )
    base = _request()
    request = TurnEngineRequest(
        core=base.core,
        turn=base.turn,
        capability=base.capability,
        execution_context=base.execution_context,
        context=base.context,
        available_tools=catalog.definitions(),
        effect_catalog=catalog,
        interaction_metadata=base.interaction_metadata,
    )

    result = await TurnEngine(host).run(request)

    assert result.final_output == "done"
    assert len(host.effect_requests) == 1
    assert host.effect_requests[0].entry is entry
    assert host.effect_requests[0].name == call.name
    assert host.effect_requests[0].call_id == call.id
    assert dict(host.effect_requests[0].arguments) == call.arguments
    assert [event.payload["effect_provenance"] for event in host.runtime_events] == [
        "authored:agent/tools/lookup",
        "authored:agent/tools/lookup",
    ]


@pytest.mark.asyncio
async def test_turn_engine_records_typed_denial_status_and_error():
    call = ToolCall(id="call_1", name="lookup", arguments={})
    host = _FakeTurnHost(
        [LLMResponse(tool_calls=[call]), LLMResponse(content="done")],
        tool_results={
            "lookup": ToolResult(
                content="capability denied: probe.effect",
                is_error=True,
                data={
                    "executionStarted": False,
                    "denial": "capability",
                },
            )
        },
    )

    result = await TurnEngine(host).run(_request())

    assert result.final_output == "done"
    event = host.runtime_events[-1]
    assert event.type == "tool.call.failed"
    assert event.payload["status"] == "failed"
    assert event.payload["effect_status"] == "denied"
    assert event.payload["effect_error"] == {
        "code": "denied",
        "message": "capability denied: probe.effect",
        "execution_started": False,
        "provenance": "authored:agent/tools/lookup",
    }


@pytest.mark.asyncio
async def test_turn_engine_records_typed_not_found_for_unknown_model_call():
    call = ToolCall(id="call_missing", name="missing", arguments={})
    host = _FakeTurnHost(
        [LLMResponse(tool_calls=[call]), LLMResponse(content="done")],
    )

    result = await TurnEngine(host).run(_request())

    assert result.final_output == "done"
    assert host.effect_requests == []
    event = host.runtime_events[-1]
    assert event.type == "tool.call.failed"
    assert event.payload["effect_status"] == "not_found"
    assert event.payload["effect_error"] == {
        "code": "not_found",
        "message": "tool not found or not allowed: missing",
        "execution_started": False,
        "provenance": "unresolved:assistant@rev_1:missing",
    }


@pytest.mark.asyncio
async def test_turn_engine_terminating_tool_result_sets_needs_user():
    call = ToolCall(id="call_1", name="lookup", arguments={})
    host = _FakeTurnHost(
        [LLMResponse(tool_calls=[call])],
        tool_results={
            "lookup": ToolResult(
                content="which value?",
                terminate=True,
                data={"needs_user": True},
            )
        },
    )

    result = await TurnEngine(host).run(_request())

    assert result.final_output == "which value?"
    assert result.needs_user is True
    assert len(host.provider_requests) == 1
    assert host.events[-1]["type"] == "message.completed"
    assert host.events[-1]["needs_user"] is True
