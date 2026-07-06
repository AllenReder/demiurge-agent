from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from demiurge.core import AgentInfo, CoreManifest, LoadedCore, PhasePipeline, SkillDefinition, SlotDefinition
from demiurge.runtime.interactions import InteractionItem
from demiurge.runtime.slot_context import SlotContextBuild
from demiurge.runtime.slots import (
    InputPipelineRequest,
    InputSlotRunRequest,
    OutputPipelineRequest,
    OutputSlotRunRequest,
    SlotOutcome,
    SlotPipelineRuntime,
)
from demiurge.sdk import AgentInput, InputEnvelope, TurnContext
from demiurge.security.capabilities import CapabilityFacade


def _slot(
    slot_id: str = "slot",
    *,
    kind: str = "input",
    failure_policy: str = "soft",
    root: Path | None = None,
) -> SlotDefinition:
    base = root or Path("/tmp")
    return SlotDefinition(
        kind=kind,
        slot_id=slot_id,
        path=base / kind / slot_id,
        relative_path=f"agent/{kind}/{slot_id}",
        manifest={},
        failure_policy=failure_policy,
    )


def _core(tmp_path: Path, *, skills: list[SkillDefinition] | None = None) -> LoadedCore:
    manifest = CoreManifest(
        agent=AgentInfo(id="assistant"),
        capabilities={"defaults": {"skill.activate": True}},
    )
    return LoadedCore(
        root=tmp_path / "core",
        manifest_path=tmp_path / "core" / "agent.yaml",
        manifest=manifest,
        raw_manifest=manifest.model_dump(),
        soul="",
        bootstrap_slots=[],
        bootstrap_pipeline=PhasePipeline(),
        bootstrap_enabled=False,
        input_slots=[],
        output_slots=[],
        input_pipeline=PhasePipeline(),
        output_pipeline=PhasePipeline(),
        tool_slots=[],
        skills=list(skills or []),
        schedules=[],
        mcp_servers=[],
    )


def _skill(tmp_path: Path, skill_id: str) -> SkillDefinition:
    return SkillDefinition(
        skill_id=skill_id,
        name=skill_id,
        path=tmp_path / "skills" / skill_id / "SKILL.md",
        relative_path=f"agent/skills/{skill_id}/SKILL.md",
        description="debugging help",
        content="Use focused diagnosis.",
    )


def _turn() -> TurnContext:
    return TurnContext(
        session_id="session_1",
        turn_id="turn_1",
        core_id="assistant",
        core_revision="rev_1",
        user_input=AgentInput(content="raw"),
    )


class _ResultClient:
    def __init__(self, *, writable: bool = True, forks: list["_ResultClient"] | None = None):
        self.writable = writable
        self.forks = forks if forks is not None else []

    def fork(self, *, writable: bool) -> "_ResultClient":
        fork = _ResultClient(writable=writable, forks=self.forks)
        self.forks.append(fork)
        return fork


class _IOClient:
    def __init__(
        self,
        *,
        items: list[InteractionItem] | None = None,
        slot_end_items: list[InteractionItem] | None = None,
    ) -> None:
        self.items = list(items or [InteractionItem(kind="slot_item")])
        self.slot_end_items = list(slot_end_items or [InteractionItem(kind="slot_end")])


class _SlotContext:
    def __init__(self, io_client: _IOClient | None = None) -> None:
        self.io_client = io_client
        self.input_requests: list[InputSlotRunRequest] = []
        self.output_requests: list[OutputSlotRunRequest] = []

    def build_input_context(self, request: InputSlotRunRequest, *, items: list[InteractionItem]) -> SlotContextBuild:
        self.input_requests.append(request)
        io_client = self.io_client or _IOClient(items=[InteractionItem(kind=f"input:{request.slot.slot_id}")])
        return SlotContextBuild(
            context=SimpleNamespace(envelope=request.envelope, request=request),
            io_client=io_client,
        )

    def build_output_context(self, request: OutputSlotRunRequest, *, items: list[InteractionItem]) -> SlotContextBuild:
        self.output_requests.append(request)
        io_client = self.io_client or _IOClient(items=[InteractionItem(kind=f"output:{request.slot.slot_id}")])
        return SlotContextBuild(
            context=SimpleNamespace(output=request.current_output, request=request),
            io_client=io_client,
        )


class _SlotEffects:
    def __init__(self) -> None:
        self.scheduled: list[list[str]] = []
        self.failed: list[tuple[list[str], str]] = []
        self.flushed: list[list[str]] = []

    def schedule_slot_end_delivery_items(self, items, *, turn, interaction_metadata):
        self.scheduled.append([item.kind for item in items])

    def mark_pending_failed(self, items, *, reason: str) -> None:
        self.failed.append(([item.kind for item in items], reason))

    async def flush_background_items(self, items, *, turn, interaction_metadata):
        self.flushed.append([item.kind for item in items])


class _SlotRuntime:
    def __init__(
        self,
        *,
        value: Any = None,
        errors: dict[str, Exception] | None = None,
        activate: str | None = None,
        write_input: bool = True,
    ) -> None:
        self.value = value
        self.errors = dict(errors or {})
        self.activate = activate
        self.write_input = write_input
        self.invocations = []

    async def invoke(self, invocation):
        self.invocations.append(invocation)
        request = getattr(invocation.context, "request", None)
        if self.activate is not None and isinstance(request, InputSlotRunRequest):
            invocation.context.envelope.activated_skills.append(self.activate)
        error = self.errors.get(invocation.slot.slot_id)
        if error is not None:
            return SlotOutcome(
                slot_id=invocation.slot.slot_id,
                phase=invocation.phase,
                status="failed",
                error=str(error),
                exception=error,
                background=invocation.background,
            )
        if self.write_input and isinstance(request, InputSlotRunRequest) and request.builder_writable:
            request.builder.add("user", f"{request.slot.slot_id}:{request.raw_input.text}")
            request.builder.add("system", f"system:{request.slot.slot_id}")
        return SlotOutcome(
            slot_id=invocation.slot.slot_id,
            phase=invocation.phase,
            status="completed",
            value=self.value,
            background=invocation.background,
        )


class _Harness:
    def __init__(self, slot_runtime: _SlotRuntime | None = None, *, io_client: _IOClient | None = None) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.background_tasks: list[asyncio.Task[Any]] = []
        self.effects = _SlotEffects()
        self.context = _SlotContext(io_client=io_client)
        self.history_refreshes = 0
        self.slot_runtime = slot_runtime or _SlotRuntime()
        self.runtime = SlotPipelineRuntime(
            slot_runtime=self.slot_runtime,
            slot_context=self.context,
            slot_effects=self.effects,
            emit_event=self.emit_event,
            track_background_task=self.track_background_task,
            refresh_history=self.refresh_history,
        )

    def emit_event(self, event_type: str, **payload: Any) -> dict[str, Any]:
        self.events.append((event_type, dict(payload)))
        return {"type": event_type, **payload}

    def track_background_task(self, task: asyncio.Task[Any]) -> None:
        self.background_tasks.append(task)

    def refresh_history(self) -> None:
        self.history_refreshes += 1


@pytest.mark.asyncio
async def test_input_pipeline_runs_serial_slots_and_flushes_parallel_without_prompt_write(tmp_path):
    harness = _Harness()

    result = await harness.runtime.run_input(
        InputPipelineRequest(
            core=_core(tmp_path),
            turn=_turn(),
            capability=CapabilityFacade(_core(tmp_path)),
            envelope=InputEnvelope(raw_text="hello", metadata={"channel": "test"}),
            state_stores=object(),
            interaction_metadata={"channel": "test"},
            injected_system_context=["injected"],
            serial_slots=[_slot("serial_one"), _slot("serial_two")],
            parallel_slots=[_slot("parallel_one")],
        )
    )

    assert result.user_text == "serial_one:hello\n\nserial_two:hello"
    assert result.persisted_user_text == result.user_text
    assert [item.kind for item in result.items] == ["input:serial_one", "input:serial_two"]
    assert [item.content for item in result.context] == [
        "injected",
        "system:serial_one\n\nsystem:serial_two",
    ]
    assert [(request.slot.slot_id, request.builder_writable, request.background) for request in harness.context.input_requests] == [
        ("serial_one", True, False),
        ("serial_two", True, False),
        ("parallel_one", False, True),
    ]
    assert harness.effects.flushed == [["input:parallel_one"]]
    assert [event[0] for event in harness.events] == [
        "module.async_scheduled",
        "module.completed",
        "module.completed",
        "module.completed",
    ]


@pytest.mark.asyncio
async def test_input_pipeline_requires_user_message(tmp_path):
    harness = _Harness()

    with pytest.raises(RuntimeError, match="input pipeline did not produce a user message"):
        await harness.runtime.run_input(
            InputPipelineRequest(
                core=_core(tmp_path),
                turn=_turn(),
                capability=CapabilityFacade(_core(tmp_path)),
                envelope=InputEnvelope(raw_text="hello"),
                state_stores=object(),
                interaction_metadata={},
                serial_slots=[],
                parallel_slots=[],
            )
        )


@pytest.mark.asyncio
async def test_input_pipeline_resolves_slot_id_override_inside_slot_runtime(tmp_path):
    harness = _Harness()
    core = _core(tmp_path)
    selected = _slot("selected")
    default_parallel = _slot("default_parallel")
    core.input_slots = [selected, default_parallel]
    core.input_pipeline = PhasePipeline(serial=[], parallel=[default_parallel])

    result = await harness.runtime.run_input(
        InputPipelineRequest(
            core=core,
            turn=_turn(),
            capability=CapabilityFacade(core),
            envelope=InputEnvelope(raw_text="hello"),
            state_stores=object(),
            interaction_metadata={},
            slot_ids=["selected"],
        )
    )

    assert result.user_text == "selected:hello"
    assert [(request.slot.slot_id, request.background) for request in harness.context.input_requests] == [
        ("selected", False)
    ]


@pytest.mark.asyncio
async def test_output_pipeline_runs_serial_slots_and_flushes_parallel_with_forked_result_client(tmp_path):
    harness = _Harness()
    result_client = _ResultClient()

    items = await harness.runtime.run_output(
        OutputPipelineRequest(
            core=_core(tmp_path),
            turn=_turn(),
            capability=CapabilityFacade(_core(tmp_path)),
            current_output="assistant",
            tool_records=[],
            state_stores=object(),
            interaction_metadata={"channel": "test"},
            result_client=result_client,
            serial_slots=[_slot("serial", kind="output")],
            parallel_slots=[_slot("parallel", kind="output")],
        )
    )

    assert [item.kind for item in items] == ["output:serial"]
    assert [(request.slot.slot_id, request.result_client.writable, request.background) for request in harness.context.output_requests] == [
        ("serial", True, False),
        ("parallel", False, True),
    ]
    assert harness.effects.flushed == [["output:parallel"]]
    assert len(result_client.forks) == 1
    assert result_client.forks[0].writable is False
    assert harness.history_refreshes == 2
    assert [event[0] for event in harness.events] == [
        "module.async_scheduled",
        "module.completed",
        "module.completed",
    ]


@pytest.mark.asyncio
async def test_input_slot_activation_adds_skill_context_after_capability_check(tmp_path):
    core = _core(tmp_path, skills=[_skill(tmp_path, "debugging")])
    harness = _Harness(_SlotRuntime(activate="debugging"))

    result = await harness.runtime.run_input(
        InputPipelineRequest(
            core=core,
            turn=_turn(),
            capability=CapabilityFacade(core),
            envelope=InputEnvelope(raw_text="hello"),
            state_stores=object(),
            interaction_metadata={"channel": "test"},
            serial_slots=[_slot(root=tmp_path)],
        )
    )

    skill_contributions = [item for item in result.context if item.type == "skill"]
    assert [item.kind for item in result.items] == ["input:slot"]
    assert [(item.type, item.key, item.content) for item in skill_contributions] == [
        ("skill", "debugging", "Use focused diagnosis.")
    ]
    assert result.context[-1].content == "system:slot"
    assert harness.context.input_requests[0].capability.audit == [
        {"capability": "skill.activate", "slot_path": "agent/input/slot", "allowed": True}
    ]
    assert [event[0] for event in harness.events] == ["skill.activated", "module.completed"]
    assert harness.effects.scheduled == [["slot_end"]]
    assert harness.effects.failed == []


@pytest.mark.asyncio
async def test_soft_input_slot_failure_returns_items_and_marks_pending_failed(tmp_path):
    core = _core(tmp_path)
    harness = _Harness(_SlotRuntime(errors={"failing": RuntimeError("boom")}))

    result = await harness.runtime.run_input(
        InputPipelineRequest(
            core=core,
            turn=_turn(),
            capability=CapabilityFacade(core),
            envelope=InputEnvelope(raw_text="hello"),
            state_stores=object(),
            interaction_metadata={"channel": "test"},
            serial_slots=[_slot("failing", root=tmp_path), _slot("healthy", root=tmp_path)],
        )
    )

    assert result.user_text == "healthy:hello"
    assert [item.kind for item in result.items] == ["input:failing", "input:healthy"]
    assert harness.effects.failed == [(["slot_end"], "slot_failed")]
    assert harness.effects.scheduled == [["slot_end"]]
    assert harness.events[0] == (
        "module.failed",
        {"turn_id": "turn_1", "slot": "agent/input/failing", "kind": "input", "error": "boom"},
    )


@pytest.mark.asyncio
async def test_hard_input_slot_failure_raises_and_marks_pending_failed(tmp_path):
    core = _core(tmp_path)
    harness = _Harness(_SlotRuntime(errors={"failing": RuntimeError("boom")}))

    with pytest.raises(RuntimeError, match="boom"):
        await harness.runtime.run_input(
            InputPipelineRequest(
                core=core,
                turn=_turn(),
                capability=CapabilityFacade(core),
                envelope=InputEnvelope(raw_text="hello"),
                state_stores=object(),
                interaction_metadata={"channel": "test"},
                serial_slots=[_slot("failing", failure_policy="hard", root=tmp_path)],
            )
        )

    assert harness.effects.failed == [(["slot_end"], "slot_failed")]
    assert harness.effects.scheduled == []
    assert harness.events == [
        (
            "module.failed",
            {"turn_id": "turn_1", "slot": "agent/input/failing", "kind": "input", "error": "boom"},
        )
    ]


@pytest.mark.asyncio
async def test_output_slot_success_refreshes_history_and_records_ignored_return(tmp_path):
    core = _core(tmp_path)
    harness = _Harness(_SlotRuntime(value="ignored"))

    result = await harness.runtime.run_output(
        OutputPipelineRequest(
            core=core,
            turn=_turn(),
            capability=CapabilityFacade(core),
            current_output="answer",
            tool_records=[],
            state_stores=object(),
            interaction_metadata={"channel": "test"},
            result_client=_ResultClient(),
            serial_slots=[_slot(kind="output", root=tmp_path)],
        )
    )

    assert [item.kind for item in result] == ["output:slot"]
    assert harness.history_refreshes == 1
    assert harness.effects.scheduled == [["slot_end"]]
    assert [event[0] for event in harness.events] == ["module.return_ignored", "module.completed"]
    assert harness.events[0][1] == {"turn_id": "turn_1", "slot": "agent/output/slot", "kind": "output"}
