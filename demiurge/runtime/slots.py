from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

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
    serial_slots: list[SlotDefinition] = field(default_factory=list)
    parallel_slots: list[SlotDefinition] = field(default_factory=list)


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
    serial_slots: list[SlotDefinition] = field(default_factory=list)
    parallel_slots: list[SlotDefinition] = field(default_factory=list)


class SlotPipelineHost(Protocol):
    def emit_event(self, event_type: str, **payload: Any) -> dict[str, Any]:
        ...

    def track_background_task(self, task: asyncio.Task[Any]) -> None:
        ...

    async def run_input_slot(
        self,
        slot: SlotDefinition,
        *,
        core: Any,
        turn: TurnContext,
        capability: Any,
        envelope: InputEnvelope,
        raw_input: RawInput,
        builder: ModuleInputBuilder,
        builder_writable: bool,
        state_stores: Any,
        interaction_metadata: dict[str, Any],
        activated: set[str],
        contributions: list[ContextContribution],
        background: bool = False,
    ) -> list[InteractionItem]:
        ...

    async def run_output_slot(
        self,
        slot: SlotDefinition,
        *,
        core: Any,
        turn: TurnContext,
        capability: Any,
        envelope: OutputEnvelope,
        current_output: str,
        tool_records: list[ToolExecutionRecord],
        state_stores: Any,
        interaction_metadata: dict[str, Any],
        result_client: Any,
        background: bool = False,
    ) -> list[InteractionItem]:
        ...

    async def flush_background_items(
        self,
        items: list[InteractionItem],
        *,
        turn: TurnContext,
        interaction_metadata: dict[str, Any],
    ) -> None:
        ...


class RunnerSlotPipelineHost:
    """Adapter from SessionTurnStepRunner to SlotPipelineHost."""

    def __init__(self, runner: Any):
        self.runner = runner

    def emit_event(self, event_type: str, **payload: Any) -> dict[str, Any]:
        return self.runner.emit_slot_event(event_type, **payload)

    def track_background_task(self, task: asyncio.Task[Any]) -> None:
        self.runner.track_slot_background_task(task)

    async def run_input_slot(
        self,
        slot: SlotDefinition,
        *,
        core: Any,
        turn: TurnContext,
        capability: Any,
        envelope: InputEnvelope,
        raw_input: RawInput,
        builder: ModuleInputBuilder,
        builder_writable: bool,
        state_stores: Any,
        interaction_metadata: dict[str, Any],
        activated: set[str],
        contributions: list[ContextContribution],
        background: bool = False,
    ) -> list[InteractionItem]:
        return await self.runner.run_input_pipeline_slot(
            slot,
            core=core,
            turn=turn,
            capability=capability,
            envelope=envelope,
            raw_input=raw_input,
            builder=builder,
            builder_writable=builder_writable,
            state_stores=state_stores,
            interaction_metadata=interaction_metadata,
            activated=activated,
            contributions=contributions,
            background=background,
        )

    async def run_output_slot(
        self,
        slot: SlotDefinition,
        *,
        core: Any,
        turn: TurnContext,
        capability: Any,
        envelope: OutputEnvelope,
        current_output: str,
        tool_records: list[ToolExecutionRecord],
        state_stores: Any,
        interaction_metadata: dict[str, Any],
        result_client: Any,
        background: bool = False,
    ) -> list[InteractionItem]:
        return await self.runner.run_output_pipeline_slot(
            slot,
            core=core,
            turn=turn,
            capability=capability,
            envelope=envelope,
            current_output=current_output,
            tool_records=tool_records,
            state_stores=state_stores,
            interaction_metadata=interaction_metadata,
            result_client=result_client,
            background=background,
        )

    async def flush_background_items(
        self,
        items: list[InteractionItem],
        *,
        turn: TurnContext,
        interaction_metadata: dict[str, Any],
    ) -> None:
        await self.runner.flush_slot_background_items(
            items,
            turn=turn,
            interaction_metadata=interaction_metadata,
        )


class SlotPipelineRuntime:
    """Runs authored slot phase pipelines behind a phase-level interface."""

    def __init__(self, host: SlotPipelineHost):
        self.host = host

    async def run_input(self, request: InputPipelineRequest) -> InputPipelineResult:
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
        for slot in request.parallel_slots:
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
            self.host.track_background_task(task)
            self.host.emit_event("module.async_scheduled", turn_id=request.turn.turn_id, slot=slot.relative_path, kind="input")
        for slot in request.serial_slots:
            items.extend(
                await self.host.run_input_slot(
                    slot,
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
        items = await self.host.run_input_slot(
            slot,
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
        await self.host.flush_background_items(
            items,
            turn=request.turn,
            interaction_metadata=request.interaction_metadata,
        )

    async def run_output(self, request: OutputPipelineRequest) -> list[InteractionItem]:
        items: list[InteractionItem] = []
        envelope = OutputEnvelope(content=request.current_output, metadata=request.interaction_metadata)
        parallel_tasks: list[asyncio.Task[Any]] = []
        for slot in request.parallel_slots:
            task = asyncio.create_task(
                self._run_background_output_slot(
                    slot,
                    request=request,
                    envelope=envelope,
                )
            )
            parallel_tasks.append(task)
            self.host.track_background_task(task)
            self.host.emit_event(
                "module.async_scheduled",
                turn_id=request.turn.turn_id,
                slot=slot.relative_path,
                kind="output",
            )
        for slot in request.serial_slots:
            items.extend(
                await self.host.run_output_slot(
                    slot,
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
        items = await self.host.run_output_slot(
            slot,
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
        await self.host.flush_background_items(
            items,
            turn=request.turn,
            interaction_metadata=request.interaction_metadata,
        )


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
