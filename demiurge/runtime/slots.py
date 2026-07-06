from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from demiurge.core import SlotDefinition, load_slot_callable
from demiurge.runtime.interactions import InteractionItem
from demiurge.sdk import (
    BootstrapContext,
    ContextContribution,
    InputContext,
    InputEnvelope,
    OutputContext,
    OutputEnvelope,
    RawInput,
    ToolContext,
    TurnContext,
)
from demiurge.tools.records import ToolExecutionRecord


SlotPhase = Literal["bootstrap", "input", "output", "tool"]
BootstrapSlotContext = BootstrapContext
InputSlotContext = InputContext
OutputSlotContext = OutputContext


@dataclass(frozen=True, slots=True)
class SlotInvocation:
    slot: SlotDefinition
    context: BootstrapSlotContext | InputSlotContext | OutputSlotContext | ToolContext | Any
    phase: SlotPhase | None = None
    background: bool = False


@dataclass(slots=True)
class SlotOutcome:
    slot_id: str
    phase: str
    status: Literal["completed", "failed"]
    value: Any = None
    error: str | None = None
    exception: BaseException | None = None
    background: bool = False

    @property
    def failed(self) -> bool:
        return self.status == "failed"

    def raise_for_error(self) -> None:
        if self.exception is not None:
            raise self.exception


class ModuleInputBuilder:
    def __init__(self) -> None:
        self.fragments: list[dict[str, str]] = []

    def add_context(
        self,
        content: str,
        *,
        role: str,
        write_history: bool,
    ) -> None:
        role = role.strip()
        if role not in {"user", "system"}:
            raise ValueError(f"invalid input context role: {role}")
        text = str(content or "").strip()
        if not text:
            return
        self.fragments.append(
            {
                "section": role,
                "content": text,
                "history_policy": "persist" if write_history else "transient",
            }
        )

    def add(
        self,
        section: str,
        content: str,
        *,
        history_policy: str | None = None,
        default_history_policy: str = "persist",
    ) -> None:
        policy = history_policy if history_policy is not None else ("transient" if section == "system" else default_history_policy)
        self.add_context(
            content,
            role=section,
            write_history=policy == "persist",
        )

    def section_text(self, section: str, *, persisted_only: bool = False) -> str:
        parts = [
            item["content"]
            for item in self.fragments
            if item["section"] == section and (not persisted_only or item["history_policy"] == "persist")
        ]
        return "\n\n".join(parts).strip()


@dataclass(slots=True)
class InputPipelineRequest:
    core: Any
    turn: TurnContext
    capability: Any
    envelope: InputEnvelope
    state_stores: Any
    interaction_metadata: dict[str, Any]
    injected_system_context: list[str] = field(default_factory=list)
    slot_ids: list[str] | tuple[str, ...] | None = None
    serial_slots: list[SlotDefinition] | None = None
    parallel_slots: list[SlotDefinition] | None = None
    phase_slots: "ResolvedPhaseSlots | None" = None


@dataclass(slots=True)
class InputPipelineResult:
    user_text: str
    persisted_user_text: str
    context: list[ContextContribution]
    items: list[InteractionItem]


@dataclass(slots=True)
class OutputPipelineRequest:
    core: Any
    turn: TurnContext
    capability: Any
    current_output: str
    tool_records: list[ToolExecutionRecord]
    state_stores: Any
    interaction_metadata: dict[str, Any]
    result_client: Any
    slot_ids: list[str] | tuple[str, ...] | None = None
    serial_slots: list[SlotDefinition] | None = None
    parallel_slots: list[SlotDefinition] | None = None
    phase_slots: "ResolvedPhaseSlots | None" = None


@dataclass(slots=True)
class ResolvedPhaseSlots:
    serial: list[SlotDefinition]
    parallel: list[SlotDefinition]

    def to_metadata(self) -> dict[str, list[str]]:
        return {
            "serial": [slot.slot_id for slot in self.serial],
            "parallel": [slot.slot_id for slot in self.parallel],
        }


@dataclass(slots=True)
class InputSlotRunRequest:
    slot: SlotDefinition
    core: Any
    turn: TurnContext
    capability: Any
    envelope: InputEnvelope
    raw_input: RawInput
    builder: ModuleInputBuilder
    builder_writable: bool
    state_stores: Any
    interaction_metadata: dict[str, Any]
    activated: set[str]
    contributions: list[ContextContribution]
    background: bool = False


@dataclass(slots=True)
class OutputSlotRunRequest:
    slot: SlotDefinition
    core: Any
    turn: TurnContext
    capability: Any
    envelope: OutputEnvelope
    current_output: str
    tool_records: list[ToolExecutionRecord]
    state_stores: Any
    interaction_metadata: dict[str, Any]
    result_client: Any
    background: bool = False


class SlotPipelineRuntime:
    """Runs authored slot phase pipelines behind a phase-level interface."""

    def __init__(
        self,
        *,
        slot_runtime: "SlotRuntime",
        slot_context: Any,
        slot_effects: Any,
        emit_event: Callable[..., dict[str, Any]],
        track_background_task: Callable[[asyncio.Task[Any]], None],
        refresh_history: Callable[[], None],
    ):
        self.slot_runtime = slot_runtime
        self.slot_context = slot_context
        self.slot_effects = slot_effects
        self.emit_event = emit_event
        self.track_background_task = track_background_task
        self.refresh_history = refresh_history

    async def run_input(self, request: InputPipelineRequest) -> InputPipelineResult:
        phase_slots = self._resolve_input_phase(request)
        builder = ModuleInputBuilder()
        raw_input = RawInput(
            text=request.envelope.raw_text,
            metadata=dict(request.envelope.metadata),
            attachments=tuple(request.envelope.attachments),
        )
        contributions: list[ContextContribution] = [
            ContextContribution(type="instruction", content=content, placement="system_context")
            for content in request.injected_system_context
            if content.strip()
        ]
        items: list[InteractionItem] = []
        activated: set[str] = set()
        parallel_tasks: list[asyncio.Task[Any]] = []
        for slot in phase_slots.parallel:
            parallel_envelope = InputEnvelope(
                raw_text=request.envelope.raw_text,
                metadata=dict(request.envelope.metadata),
                attachments=list(request.envelope.attachments),
            )
            task = asyncio.create_task(
                self._run_background_input_slot(
                    slot,
                    request=request,
                    envelope=parallel_envelope,
                    raw_input=raw_input,
                    builder=builder,
                )
            )
            parallel_tasks.append(task)
            self.track_background_task(task)
            self.emit_event("module.async_scheduled", turn_id=request.turn.turn_id, slot=slot.relative_path, kind="input")
        for slot in phase_slots.serial:
            items.extend(
                await self._run_input_slot(
                    InputSlotRunRequest(
                        slot=slot,
                        core=request.core,
                        turn=request.turn,
                        capability=request.capability,
                        envelope=request.envelope,
                        raw_input=raw_input,
                        builder=builder,
                        builder_writable=True,
                        state_stores=request.state_stores,
                        interaction_metadata=request.interaction_metadata,
                        activated=activated,
                        contributions=contributions,
                    )
                )
            )
        if parallel_tasks:
            await asyncio.gather(*parallel_tasks, return_exceptions=False)
        system_text = builder.section_text("system")
        if system_text:
            contributions.append(ContextContribution(type="instruction", content=system_text, placement="system_context"))
        user_text = builder.section_text("user")
        if not user_text:
            raise RuntimeError("input pipeline did not produce a user message")
        return InputPipelineResult(
            user_text=user_text,
            persisted_user_text=builder.section_text("user", persisted_only=True),
            context=contributions,
            items=items,
        )

    async def _run_background_input_slot(
        self,
        slot: SlotDefinition,
        *,
        request: InputPipelineRequest,
        envelope: InputEnvelope,
        raw_input: RawInput,
        builder: ModuleInputBuilder,
    ) -> None:
        items = await self._run_input_slot(
            InputSlotRunRequest(
                slot=slot,
                core=request.core,
                turn=request.turn,
                capability=request.capability,
                envelope=envelope,
                raw_input=raw_input,
                builder=builder,
                builder_writable=False,
                state_stores=request.state_stores,
                interaction_metadata=request.interaction_metadata,
                activated=set(),
                contributions=[],
                background=True,
            )
        )
        await self.slot_effects.flush_background_items(
            items,
            turn=request.turn,
            interaction_metadata=request.interaction_metadata,
        )

    async def run_output(self, request: OutputPipelineRequest) -> list[InteractionItem]:
        phase_slots = self._resolve_output_phase(request)
        items: list[InteractionItem] = []
        envelope = OutputEnvelope(content=request.current_output, metadata=request.interaction_metadata)
        parallel_tasks: list[asyncio.Task[Any]] = []
        for slot in phase_slots.parallel:
            task = asyncio.create_task(
                self._run_background_output_slot(
                    slot,
                    request=request,
                    envelope=envelope,
                )
            )
            parallel_tasks.append(task)
            self.track_background_task(task)
            self.emit_event(
                "module.async_scheduled",
                turn_id=request.turn.turn_id,
                slot=slot.relative_path,
                kind="output",
            )
        for slot in phase_slots.serial:
            items.extend(
                await self._run_output_slot(
                    OutputSlotRunRequest(
                        slot=slot,
                        core=request.core,
                        turn=request.turn,
                        capability=request.capability,
                        envelope=envelope,
                        current_output=request.current_output,
                        tool_records=request.tool_records,
                        state_stores=request.state_stores,
                        interaction_metadata=request.interaction_metadata,
                        result_client=request.result_client,
                    )
                )
            )
        if parallel_tasks:
            await asyncio.gather(*parallel_tasks, return_exceptions=False)
        return items

    async def _run_background_output_slot(
        self,
        slot: SlotDefinition,
        *,
        request: OutputPipelineRequest,
        envelope: OutputEnvelope,
    ) -> None:
        items = await self._run_output_slot(
            OutputSlotRunRequest(
                slot=slot,
                core=request.core,
                turn=request.turn,
                capability=request.capability,
                envelope=envelope,
                current_output=request.current_output,
                tool_records=request.tool_records,
                state_stores=request.state_stores,
                interaction_metadata=request.interaction_metadata,
                result_client=request.result_client.fork(writable=False),
                background=True,
            )
        )
        await self.slot_effects.flush_background_items(
            items,
            turn=request.turn,
            interaction_metadata=request.interaction_metadata,
        )

    def _resolve_input_phase(self, request: InputPipelineRequest) -> ResolvedPhaseSlots:
        return self._resolve_phase(
            core=request.core,
            kind="input",
            phase_slots=request.phase_slots,
            slot_ids=request.slot_ids,
            serial_slots=request.serial_slots,
            parallel_slots=request.parallel_slots,
        )

    def _resolve_output_phase(self, request: OutputPipelineRequest) -> ResolvedPhaseSlots:
        return self._resolve_phase(
            core=request.core,
            kind="output",
            phase_slots=request.phase_slots,
            slot_ids=request.slot_ids,
            serial_slots=request.serial_slots,
            parallel_slots=request.parallel_slots,
        )

    def _resolve_phase(
        self,
        *,
        core: Any,
        kind: Literal["input", "output"],
        phase_slots: ResolvedPhaseSlots | None,
        slot_ids: list[str] | tuple[str, ...] | None,
        serial_slots: list[SlotDefinition] | None,
        parallel_slots: list[SlotDefinition] | None,
    ) -> ResolvedPhaseSlots:
        if phase_slots is not None:
            if slot_ids is not None or serial_slots is not None or parallel_slots is not None:
                raise ValueError(f"{kind} phase_slots cannot be combined with slot_ids, serial_slots, or parallel_slots")
            return phase_slots
        if slot_ids is not None:
            if serial_slots is not None or parallel_slots is not None:
                raise ValueError(f"{kind} slot_ids cannot be combined with serial_slots or parallel_slots")
            return ResolvedPhaseSlots(serial=self._resolve_slot_ids(core, kind, slot_ids), parallel=[])
        pipeline = core.input_pipeline if kind == "input" else core.output_pipeline
        serial = list(serial_slots) if serial_slots is not None else list(pipeline.serial)
        if parallel_slots is not None:
            parallel = list(parallel_slots)
        elif serial_slots is not None:
            parallel = []
        else:
            parallel = list(pipeline.parallel)
        return ResolvedPhaseSlots(serial=serial, parallel=parallel)

    def _resolve_slot_ids(
        self,
        core: Any,
        kind: Literal["input", "output"],
        slot_ids: list[str] | tuple[str, ...],
    ) -> list[SlotDefinition]:
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

    async def _run_input_slot(self, request: InputSlotRunRequest) -> list[InteractionItem]:
        slot = request.slot
        turn = request.turn
        items: list[InteractionItem] = []
        context_build = self.slot_context.build_input_context(request, items=items)
        ctx = context_build.context
        io_client = context_build.io_client
        try:
            prior_envelope_activations = set(request.envelope.activated_skills)
            value = await self._call_slot(slot, ctx, background=request.background)
            if value is not None:
                self.emit_event("module.return_ignored", turn_id=turn.turn_id, slot=slot.relative_path, kind="input")
            for name in [name for name in request.envelope.activated_skills if name not in prior_envelope_activations]:
                if name in request.activated:
                    continue
                self._require_skill_activation(request.capability, slot.relative_path, name)
                skill = request.core.skill_by_id(name)
                if skill is None:
                    request.activated.add(name)
                    self.emit_event(
                        "skill.activation_ignored",
                        turn_id=turn.turn_id,
                        slot=slot.relative_path,
                        skill=name,
                    )
                    continue
                request.activated.add(name)
                request.contributions.append(
                    ContextContribution(type="skill", key=skill.name, content=skill.content, placement="system_context")
                )
                self.emit_event("skill.activated", turn_id=turn.turn_id, slot=slot.relative_path, skill=skill.name)
            self.slot_effects.schedule_slot_end_delivery_items(
                io_client.slot_end_items,
                turn=turn,
                interaction_metadata=request.interaction_metadata,
            )
            self.emit_event("module.completed", turn_id=turn.turn_id, slot=slot.relative_path, kind="input")
            return io_client.items
        except Exception as exc:
            self.slot_effects.mark_pending_failed(io_client.slot_end_items, reason="slot_failed")
            self.emit_event(
                "module.failed",
                turn_id=turn.turn_id,
                slot=slot.relative_path,
                kind="input",
                error=str(exc),
            )
            if slot.failure_policy == "hard":
                raise
            return io_client.items

    async def _run_output_slot(self, request: OutputSlotRunRequest) -> list[InteractionItem]:
        slot = request.slot
        turn = request.turn
        items: list[InteractionItem] = []
        context_build = self.slot_context.build_output_context(request, items=items)
        ctx = context_build.context
        io_client = context_build.io_client
        try:
            value = await self._call_slot(slot, ctx, background=request.background)
            if value is not None:
                self.emit_event("module.return_ignored", turn_id=turn.turn_id, slot=slot.relative_path, kind="output")
            self.slot_effects.schedule_slot_end_delivery_items(
                io_client.slot_end_items,
                turn=turn,
                interaction_metadata=request.interaction_metadata,
            )
            self.refresh_history()
            self.emit_event(
                "module.completed",
                turn_id=turn.turn_id,
                slot=slot.relative_path,
                kind="output",
            )
            return io_client.items
        except Exception as exc:
            self.slot_effects.mark_pending_failed(io_client.slot_end_items, reason="slot_failed")
            self.emit_event(
                "module.failed",
                turn_id=turn.turn_id,
                slot=slot.relative_path,
                kind="output",
                error=str(exc),
            )
            if slot.failure_policy == "hard":
                raise
            return io_client.items

    async def _call_slot(self, slot: SlotDefinition, ctx: Any, *, background: bool) -> Any:
        outcome = await self.slot_runtime.invoke(SlotInvocation(slot=slot, context=ctx, phase=slot.kind, background=background))
        outcome.raise_for_error()
        return outcome.value

    def _require_skill_activation(self, capability: Any, slot_path: str, name: str) -> None:
        scoped = f"skill.activate:{name}"
        if capability.can(scoped, slot_path=slot_path):
            capability.require(scoped, slot_path=slot_path)
            return
        capability.require("skill.activate", slot_path=slot_path)


class SlotRuntime:
    """Executes authored slot handlers behind a small runtime interface."""

    async def invoke(self, invocation: SlotInvocation) -> SlotOutcome:
        slot = invocation.slot
        phase = invocation.phase or slot.kind
        try:
            func = load_slot_callable(slot)
            value = func(invocation.context)
            if inspect.isawaitable(value):
                value = await value
            return SlotOutcome(
                slot_id=slot.slot_id,
                phase=phase,
                status="completed",
                value=value,
                background=invocation.background,
            )
        except Exception as exc:
            return SlotOutcome(
                slot_id=slot.slot_id,
                phase=phase,
                status="failed",
                error=str(exc),
                exception=exc,
                background=invocation.background,
            )
