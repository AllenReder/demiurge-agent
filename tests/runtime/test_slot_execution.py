from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from demiurge.core import AgentInfo, CoreManifest, LoadedCore, PhasePipeline, SkillDefinition, SlotDefinition
from demiurge.runtime.interactions import InteractionItem
from demiurge.runtime.slot_context import SlotContextBuild
from demiurge.runtime.slot_execution import SlotExecutionRuntime
from demiurge.runtime.slots import (
    InputSlotRunRequest,
    ModuleInputBuilder,
    OutputSlotRunRequest,
    SlotOutcome,
)
from demiurge.sdk import AgentInput, InputEnvelope, OutputEnvelope, RawInput, TurnContext
from demiurge.security.capabilities import CapabilityFacade


def _slot(tmp_path: Path, *, kind: str = "input", failure_policy: str = "soft") -> SlotDefinition:
    root = tmp_path / kind / "slot"
    root.mkdir(parents=True, exist_ok=True)
    return SlotDefinition(
        kind=kind,
        slot_id="slot",
        path=root,
        relative_path=f"agent/{kind}/slot",
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
        user_input=AgentInput(content="hello"),
    )


class _IOClient:
    def __init__(self, *, items: list[InteractionItem] | None = None) -> None:
        self.items = list(items or [InteractionItem(kind="slot_item")])
        self.slot_end_items = [InteractionItem(kind="slot_end")]


class _SlotContext:
    def __init__(self, io_client: _IOClient | None = None) -> None:
        self.io_client = io_client or _IOClient()
        self.input_requests: list[InputSlotRunRequest] = []
        self.output_requests: list[OutputSlotRunRequest] = []

    def build_input_context(self, request: InputSlotRunRequest, *, items: list[InteractionItem]) -> SlotContextBuild:
        self.input_requests.append(request)
        return SlotContextBuild(
            context=SimpleNamespace(envelope=request.envelope),
            io_client=self.io_client,
        )

    def build_output_context(self, request: OutputSlotRunRequest, *, items: list[InteractionItem]) -> SlotContextBuild:
        self.output_requests.append(request)
        return SlotContextBuild(
            context=SimpleNamespace(output=request.current_output),
            io_client=self.io_client,
        )


class _SlotEffects:
    def __init__(self) -> None:
        self.scheduled: list[list[str]] = []
        self.failed: list[tuple[list[str], str]] = []

    def schedule_slot_end_delivery_items(self, items, *, turn, interaction_metadata):
        self.scheduled.append([item.kind for item in items])

    def mark_pending_failed(self, items, *, reason: str) -> None:
        self.failed.append(([item.kind for item in items], reason))


class _SlotRuntime:
    def __init__(self, *, value: Any = None, error: Exception | None = None, activate: str | None = None) -> None:
        self.value = value
        self.error = error
        self.activate = activate
        self.invocations = []

    async def invoke(self, invocation):
        self.invocations.append(invocation)
        if self.activate is not None:
            invocation.context.envelope.activated_skills.append(self.activate)
        if self.error is not None:
            return SlotOutcome(
                slot_id=invocation.slot.slot_id,
                phase=invocation.phase,
                status="failed",
                error=str(self.error),
                exception=self.error,
                background=invocation.background,
            )
        return SlotOutcome(
            slot_id=invocation.slot.slot_id,
            phase=invocation.phase,
            status="completed",
            value=self.value,
            background=invocation.background,
        )


class _Harness:
    def __init__(self, slot_runtime: _SlotRuntime, *, io_client: _IOClient | None = None) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.effects = _SlotEffects()
        self.context = _SlotContext(io_client=io_client)
        self.history_refreshes = 0
        self.runtime = SlotExecutionRuntime(
            slot_runtime=slot_runtime,
            slot_context=self.context,
            slot_effects=self.effects,
            emit_event=self.emit_event,
            refresh_history=self.refresh_history,
        )

    def emit_event(self, event_type: str, **payload: Any) -> dict[str, Any]:
        self.events.append((event_type, dict(payload)))
        return {"type": event_type, **payload}

    def refresh_history(self) -> None:
        self.history_refreshes += 1


def _input_request(tmp_path: Path, *, slot: SlotDefinition, core: LoadedCore) -> InputSlotRunRequest:
    return InputSlotRunRequest(
        slot=slot,
        core=core,
        turn=_turn(),
        capability=CapabilityFacade(core),
        envelope=InputEnvelope(raw_text="hello"),
        raw_input=RawInput(text="hello"),
        builder=ModuleInputBuilder(),
        builder_writable=True,
        state_stores=object(),
        interaction_metadata={"channel": "test"},
        activated=set(),
        contributions=[],
    )


def _output_request(tmp_path: Path, *, slot: SlotDefinition, core: LoadedCore) -> OutputSlotRunRequest:
    return OutputSlotRunRequest(
        slot=slot,
        core=core,
        turn=_turn(),
        capability=CapabilityFacade(core),
        envelope=OutputEnvelope(content="answer"),
        current_output="answer",
        tool_records=[],
        state_stores=object(),
        interaction_metadata={"channel": "test"},
        result_client=object(),
    )


@pytest.mark.asyncio
async def test_input_slot_activation_adds_skill_context_after_capability_check(tmp_path):
    core = _core(tmp_path, skills=[_skill(tmp_path, "debugging")])
    request = _input_request(tmp_path, slot=_slot(tmp_path), core=core)
    harness = _Harness(_SlotRuntime(activate="debugging"))

    result = await harness.runtime.run_input(request)

    assert [item.kind for item in result] == ["slot_item"]
    assert request.activated == {"debugging"}
    assert [(item.type, item.key, item.content) for item in request.contributions] == [
        ("skill", "debugging", "Use focused diagnosis.")
    ]
    assert request.capability.audit == [
        {"capability": "skill.activate", "slot_path": "agent/input/slot", "allowed": True}
    ]
    assert [event[0] for event in harness.events] == ["skill.activated", "module.completed"]
    assert harness.effects.scheduled == [["slot_end"]]
    assert harness.effects.failed == []


@pytest.mark.asyncio
async def test_soft_input_slot_failure_returns_items_and_marks_pending_failed(tmp_path):
    core = _core(tmp_path)
    request = _input_request(tmp_path, slot=_slot(tmp_path, failure_policy="soft"), core=core)
    io_client = _IOClient(items=[InteractionItem(kind="partial")])
    harness = _Harness(_SlotRuntime(error=RuntimeError("boom")), io_client=io_client)

    result = await harness.runtime.run_input(request)

    assert [item.kind for item in result] == ["partial"]
    assert harness.effects.failed == [(["slot_end"], "slot_failed")]
    assert harness.effects.scheduled == []
    assert harness.events == [
        (
            "module.failed",
            {"turn_id": "turn_1", "slot": "agent/input/slot", "kind": "input", "error": "boom"},
        )
    ]


@pytest.mark.asyncio
async def test_output_slot_success_refreshes_history_and_records_ignored_return(tmp_path):
    core = _core(tmp_path)
    request = _output_request(tmp_path, slot=_slot(tmp_path, kind="output"), core=core)
    harness = _Harness(_SlotRuntime(value="ignored"))

    result = await harness.runtime.run_output(request)

    assert [item.kind for item in result] == ["slot_item"]
    assert harness.history_refreshes == 1
    assert harness.effects.scheduled == [["slot_end"]]
    assert [event[0] for event in harness.events] == ["module.return_ignored", "module.completed"]
    assert harness.events[0][1] == {"turn_id": "turn_1", "slot": "agent/output/slot", "kind": "output"}
