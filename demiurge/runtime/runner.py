from __future__ import annotations

import asyncio
import json
import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping

from demiurge.runtime.tasks import (
    RuntimeTaskConflictError,
    RuntimeTaskContext,
    RuntimeTaskKindError,
    RuntimeTaskOutcome,
    RuntimeTaskWorker,
)
from demiurge.security.capabilities import CapabilityDenied, CapabilityFacade
from demiurge.runtime.context import ContextAssembler
from demiurge.core import CoreLoader, LoadedCore, SlotDefinition
from demiurge.runtime.control import ActionSource, ActionSpec
from demiurge.runtime.delivery import (
    CONTENT_BLOCK_TYPES,
    DELIVERY_MODES,
    ArtifactInput,
    ArtifactRef,
    ContentBlock,
    DeliveryHandle,
    DeliveryRequest,
    DeliveryRouteContext,
    artifact_input_to_dict,
    is_relative_to,
    is_url,
)
from demiurge.runtime.interactions import (
    InteractionDelivery,
    InteractionInbound,
    InteractionItem,
    InteractionOutbound,
    SessionInteractionRouter,
    SessionRouteBinding,
    ToolInteractionRecord,
)
from demiurge.runtime.durable_work import DurableWorkSpec, durable_work_enqueued_event
from demiurge.runtime.outbox import DeliveryRuntime
from demiurge.runtime.session import SessionRuntime
from demiurge.runtime.slots import (
    InputPipelineRequest,
    InputSlotRunRequest,
    ModuleInputBuilder,
    OutputPipelineRequest,
    OutputSlotRunRequest,
    RunnerSlotPipelineHost,
    SlotInvocation,
    SlotPipelineRuntime,
    SlotRuntime,
)
from demiurge.runtime.store import RuntimeEvent
from demiurge.runtime.turn import RunnerTurnEngineHost, TurnEngine, TurnEngineRequest
from demiurge.runtime_timezone import RuntimeTimezone, resolve_runtime_timezone
from demiurge.providers import LLMMessage, LLMRequest, LLMResponse, Provider, ToolCall
from demiurge.sdk import (
    AgentInput,
    AgentDeliverySummary,
    AgentRunResult,
    AgentSpawnHandle,
    AgentToolSummary,
    ArtifactRef,
    BootstrapContext,
    ContextContribution,
    DeliverEffect,
    EffectRequest,
    InputEnvelope,
    InputContext,
    INPUT_HISTORY_POLICIES,
    INPUT_SECTIONS,
    OutputEnvelope,
    OutputContext,
    RawInput,
    HistoryMessageSummary,
    StateProposal,
    ToolResult,
    TurnContext,
)
from demiurge.storage import ArtifactStore, EventLog, SessionMessage, StateStore, VersionStore
from demiurge.tools.records import ToolExecutionRecord
from demiurge.tools.runtime import ToolRuntime
from demiurge.util import utc_id


SUMMARY_PREFIX = (
    "[CONTEXT COMPACTION - REFERENCE ONLY] Earlier turns were compacted into the summary below. "
    "Treat it as background reference, not as active instructions. Respond only to the latest user "
    "message that appears after this summary; the latest user message wins if there is any conflict."
)
SUMMARY_END_MARKER = "--- END OF CONTEXT SUMMARY - respond to the message below, not the summary above ---"
DELEGATION_TOOL_NAMES = {"delegate_task", "task_status", "task_control", "yield_until"}
CHILD_AGENT_DEFAULT_INPUT_SLOTS = ("base_input",)
CHILD_AGENT_DEFAULT_OUTPUT_SLOTS = ("base_output",)
CHILD_AGENT_ALL_SLOTS = "all"
CHILD_AGENT_ALL_TOOLS = "all"
CHILD_AGENT_NO_TOOLS = "none"


ChildSlotRequest = str | Sequence[str] | None
ChildToolRequest = str | Sequence[str] | None


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
class ResolvedChildAgentSlots:
    input: ResolvedPhaseSlots
    output: ResolvedPhaseSlots
    use_bootstrap: bool

    def to_metadata(self) -> dict[str, Any]:
        return {
            "input_slots": self.input.to_metadata(),
            "output_slots": self.output.to_metadata(),
            "use_bootstrap": self.use_bootstrap,
        }


@dataclass(slots=True)
class ResolvedChildAgentTools:
    requested: str | list[str]
    resolved: list[str]
    tool_policy: dict[str, Any] | None = None


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


@dataclass(slots=True)
class CompactionResult:
    session_id: str
    turn_id: str
    compacted_count: int
    summary_message_id: str | None
    summary: str
    skipped: bool = False
    error: str | None = None


@dataclass(slots=True)
class ModuleStateStores:
    core: StateStore
    session: StateStore


class ScopedModuleStateClient:
    def __init__(
        self,
        *,
        state_store: StateStore,
        capability: CapabilityFacade,
        capability_prefix: str,
        slot_path: str,
        turn_id: str,
        emit_event: Callable[..., dict[str, Any]],
    ):
        self.state_store = state_store
        self.capability = capability
        self.capability_prefix = capability_prefix
        self.slot_path = slot_path
        self.turn_id = turn_id
        self.emit_event = emit_event

    def get(self, target: str, default: Any = None) -> Any:
        self._require(f"{self.capability_prefix}.read", target)
        return self.state_store.read_target(target, default)

    def set(self, target: str, value: Any) -> Any:
        return self._write(StateProposal(target=target, operation="set", patch=value))

    def merge(self, target: str, value: Mapping[str, Any]) -> Any:
        return self._write(StateProposal(target=target, operation="merge", patch=dict(value)))

    def append(self, target: str, value: Any) -> Any:
        return self._write(StateProposal(target=target, operation="append", patch=value))

    def snapshot(self) -> Mapping[str, Any]:
        self.capability.require(f"{self.capability_prefix}.read", slot_path=self.slot_path)
        return self.state_store.snapshot()

    def _write(self, proposal: StateProposal) -> Any:
        self._require(f"{self.capability_prefix}.write", proposal.target)
        entry = self.state_store.submit(proposal, source=self.slot_path, turn_id=self.turn_id)
        self.emit_event(
            "state.module_updated",
            turn_id=self.turn_id,
            slot=self.slot_path,
            scope=self.state_store.scope,
            proposal_id=entry["id"],
            target=proposal.target,
            operation=proposal.operation,
        )
        return self.state_store.read_target(proposal.target)

    def _require(self, capability_name: str, target: str) -> None:
        if target:
            scoped = f"{capability_name}:{target}"
            if self.capability.can(scoped, slot_path=self.slot_path):
                self.capability.require(scoped, slot_path=self.slot_path)
                return
        self.capability.require(capability_name, slot_path=self.slot_path)


class ModuleStateClient:
    def __init__(
        self,
        *,
        state_stores: ModuleStateStores,
        capability: CapabilityFacade,
        slot_path: str,
        turn_id: str,
        emit_event: Callable[..., dict[str, Any]],
    ):
        self.core = ScopedModuleStateClient(
            state_store=state_stores.core,
            capability=capability,
            capability_prefix="state.core",
            slot_path=slot_path,
            turn_id=turn_id,
            emit_event=emit_event,
        )
        self.session = ScopedModuleStateClient(
            state_store=state_stores.session,
            capability=capability,
            capability_prefix="state.session",
            slot_path=slot_path,
            turn_id=turn_id,
            emit_event=emit_event,
        )


class ModuleToolClient:
    def __init__(
        self,
        *,
        parent: "SessionTurnStepRunner",
        tool_runtime: ToolRuntime,
        core: LoadedCore,
        turn: TurnContext,
        capability: CapabilityFacade,
        slot_path: str,
        interaction_metadata: Mapping[str, Any],
        emit_event: Callable[..., dict[str, Any]],
        items: list[InteractionItem],
    ):
        self.parent = parent
        self.tool_runtime = tool_runtime
        self.core = core
        self.turn = turn
        self.capability = capability
        self.slot_path = slot_path
        self.interaction_metadata = dict(interaction_metadata)
        self.emit_event = emit_event
        self.items = items

    async def call(self, name: str, arguments: Mapping[str, Any] | None = None):
        self.capability.require(f"tool.call:{name}", slot_path=self.slot_path)
        return await self.parent.execute_tool(
            ToolCall(name=name, arguments=dict(arguments or {})),
            core=self.core,
            turn=self.turn,
            capability=self.capability,
            emit_event=self.emit_event,
            output_factory=lambda slot: self.parent._module_io_client(
                slot,
                turn=self.turn,
                capability=self.capability,
                interaction_metadata=self.interaction_metadata,
                items=self.items,
            ),
        )


class ModuleAgentsClient:
    def __init__(
        self,
        *,
        parent: "SessionTurnStepRunner",
        capability: CapabilityFacade,
        slot_path: str,
        turn: TurnContext,
        interaction_metadata: Mapping[str, Any],
        emit_event: Callable[..., dict[str, Any]],
    ):
        self.parent = parent
        self.capability = capability
        self.slot_path = slot_path
        self.turn = turn
        self.interaction_metadata = dict(interaction_metadata)
        self.emit_event = emit_event

    async def run(
        self,
        core_id: str,
        raw_input: str,
        *,
        context: str | list[str] | None = None,
        input_slots: ChildSlotRequest = None,
        output_slots: ChildSlotRequest = None,
        use_bootstrap: bool = False,
        tools: ChildToolRequest = CHILD_AGENT_ALL_TOOLS,
    ) -> AgentRunResult:
        self.capability.require(f"agents.run:{core_id}", slot_path=self.slot_path)
        run_id = utc_id("agent_run_")
        self.emit_event(
            "agent_run.started",
            turn_id=self.turn.turn_id,
            slot=self.slot_path,
            agent_run_id=run_id,
            child_core_id=core_id,
        )
        result = await self.parent._run_child_agent(
            core_id=core_id,
            raw_input=raw_input,
            parent_turn=self.turn,
            parent_slot_path=self.slot_path,
            context=self._normalize_context(context),
            input_slots=input_slots,
            output_slots=output_slots,
            use_bootstrap=use_bootstrap,
            tools=tools,
        )
        self.emit_event(
            "agent_run.completed",
            turn_id=self.turn.turn_id,
            slot=self.slot_path,
            agent_run_id=run_id,
            child_core_id=core_id,
            child_session_id=result.session_id,
            child_turn_id=result.turn_id,
            chars=len(result.content),
        )
        return result

    def spawn(
        self,
        core_id: str,
        raw_input: str,
        *,
        context: str | list[str] | None = None,
        input_slots: ChildSlotRequest = None,
        output_slots: ChildSlotRequest = None,
        use_bootstrap: bool = False,
        tools: ChildToolRequest = CHILD_AGENT_ALL_TOOLS,
    ) -> AgentSpawnHandle:
        self.capability.require(f"agents.spawn:{core_id}", slot_path=self.slot_path)
        return self.parent._spawn_child_agent(
            core_id=core_id,
            raw_input=raw_input,
            parent_turn=self.turn,
            parent_slot_path=self.slot_path,
            context=self._normalize_context(context),
            input_slots=input_slots,
            output_slots=output_slots,
            use_bootstrap=use_bootstrap,
            tools=tools,
        )

    def _normalize_context(self, context: str | list[str] | None) -> list[str]:
        if context is None:
            return []
        if isinstance(context, str):
            return [context]
        return [str(item) for item in context if str(item).strip()]


class ModuleBootstrapClient:
    def __init__(self, *, workspace: str | None = None) -> None:
        self.fragments: list[str] = []
        self.workspace = workspace or ""

    def add(self, text: str) -> None:
        content = str(text or "")
        if content.strip():
            self.fragments.append(content)


class ModuleInputClient:
    def __init__(self, *, raw_input: RawInput, builder: ModuleInputBuilder, writable: bool, sender: "ModuleIOClient"):
        self.raw_input = raw_input
        self._builder = builder
        self._writable = writable
        self._sender = sender

    @property
    def raw_text(self) -> str:
        return self.raw_input.text

    @property
    def attachments(self) -> tuple[Any, ...]:
        return tuple(self.raw_input.attachments)

    def add_context(self, content: str, *, role: str = "system", write_history: bool | None = None) -> None:
        if not self._writable:
            raise RuntimeError("parallel input modules cannot modify the current prompt")
        normalized_role = role.strip()
        if write_history is None:
            write_history = normalized_role == "user"
        self._builder.add_context(content, role=normalized_role, write_history=write_history)

    def add(self, section: str, content: str, *, history_policy: str | None = None) -> None:
        if not self._writable:
            raise RuntimeError("parallel input modules cannot modify the current prompt")
        self._builder.add(
            section,
            content,
            history_policy=history_policy,
            default_history_policy=self._sender.default_history_policy,
        )

    @property
    def workspace(self) -> Path:
        return self._sender.workspace

    @property
    def session_root(self) -> Path:
        return self._sender.session_root

    def send(self, *args, **kwargs):
        return self._sender.send(*args, **kwargs)

    def send_text(self, *args, **kwargs):
        return self._sender.send_text(*args, **kwargs)

    def progress(self, *args, **kwargs):
        return self._sender.progress(*args, **kwargs)

    def notice(self, *args, **kwargs):
        return self._sender.notice(*args, **kwargs)

    def send_image(self, *args, **kwargs):
        return self._sender.send_image(*args, **kwargs)

    def send_audio(self, *args, **kwargs):
        return self._sender.send_audio(*args, **kwargs)

    def send_video(self, *args, **kwargs):
        return self._sender.send_video(*args, **kwargs)

    def send_file(self, *args, **kwargs):
        return self._sender.send_file(*args, **kwargs)


class ModuleOutputClient:
    def __init__(self, *, content: str, metadata: Mapping[str, Any] | None = None, sender: "ModuleIOClient"):
        self.content = content
        self.metadata = dict(metadata or {})
        self._sender = sender

    @property
    def response_text(self) -> str:
        return self.content

    @property
    def workspace(self) -> Path:
        return self._sender.workspace

    @property
    def session_root(self) -> Path:
        return self._sender.session_root

    def send(self, *args, **kwargs):
        return self._sender.send(*args, **kwargs)

    def send_text(self, *args, **kwargs):
        return self._sender.send_text(*args, **kwargs)

    def progress(self, *args, **kwargs):
        return self._sender.progress(*args, **kwargs)

    def notice(self, *args, **kwargs):
        return self._sender.notice(*args, **kwargs)

    def send_image(self, *args, **kwargs):
        return self._sender.send_image(*args, **kwargs)

    def send_audio(self, *args, **kwargs):
        return self._sender.send_audio(*args, **kwargs)

    def send_video(self, *args, **kwargs):
        return self._sender.send_video(*args, **kwargs)

    def send_file(self, *args, **kwargs):
        return self._sender.send_file(*args, **kwargs)


class ModuleHistoryClient:
    def __init__(self, *, sessions: SessionRuntime, session_id: str):
        self.sessions = sessions
        self.session_id = session_id

    def recent_messages(self, limit: int, roles: list[str] | tuple[str, ...] | set[str] | None = None) -> list[HistoryMessageSummary]:
        allowed = set(roles or {"user", "tool", "assistant"}) & {"user", "tool", "assistant"}
        if limit <= 0 or not allowed:
            return []
        messages = [
            message
            for message in self.sessions.read_messages(self.session_id)
            if message.kind == "message" and message.role in allowed
        ]
        result: list[HistoryMessageSummary] = []
        for message in messages[-limit:]:
            metadata = message.metadata or {}
            raw_tool_calls = metadata.get("tool_calls") if isinstance(metadata.get("tool_calls"), list) else []
            result.append(
                HistoryMessageSummary(
                    message_id=message.id,
                    role=message.role,
                    content=message.content,
                    turn_id=message.turn_id,
                    created_at=message.created_at,
                    step_id=metadata.get("step_id"),
                    tool_call_id=metadata.get("tool_call_id"),
                    tool_calls=tuple(dict(call) for call in raw_tool_calls if isinstance(call, Mapping)),
                    visible=message.visible,
                    model_visible=message.model_visible,
                    tool_name=metadata.get("tool_name"),
                    is_error=metadata.get("is_error") if message.role == "tool" else None,
                )
            )
        return result


class ModuleSkillClient:
    def __init__(
        self,
        *,
        envelope: InputEnvelope,
        capability: CapabilityFacade,
        slot_path: str,
        turn_id: str,
        emit_event: Callable[..., dict[str, Any]],
    ):
        self.envelope = envelope
        self.capability = capability
        self.slot_path = slot_path
        self.turn_id = turn_id
        self.emit_event = emit_event

    def activate(self, name: str) -> None:
        self._require(name)
        if name not in self.envelope.activated_skills:
            self.envelope.activated_skills.append(name)
        self.emit_event(
            "skill.activation_requested",
            turn_id=self.turn_id,
            slot=self.slot_path,
            skill=name,
        )

    def _require(self, name: str) -> None:
        scoped = f"skill.activate:{name}"
        if self.capability.can(scoped, slot_path=self.slot_path):
            self.capability.require(scoped, slot_path=self.slot_path)
            return
        self.capability.require("skill.activate", slot_path=self.slot_path)


class ModuleIOClient:
    """Phase-local IO SDK exposed to authored input/output modules."""

    def __init__(
        self,
        *,
        home: Path,
        session_id: str,
        workspace: str | None,
        default_history_policy: str = "persist",
        default_write_history: bool | None = None,
        allow_write_history: bool = True,
        commit: Callable[[DeliveryRequest], InteractionItem | None],
        schedule: Callable[[InteractionItem], None],
        route: DeliveryRouteContext | None = None,
        background: bool = False,
        items: list[InteractionItem] | None = None,
    ):
        self.home = home
        self.session_id = session_id
        self.workspace = Path(workspace or ".").resolve()
        self.session_root = (home / "runtime" / "artifacts" / session_id).resolve()
        self.default_history_policy = default_history_policy
        self.default_write_history = default_write_history
        self.allow_write_history = allow_write_history
        self.commit = commit
        self.schedule = schedule
        self.route = route
        self.background = background
        self.items: list[InteractionItem] = items if items is not None else []
        self.slot_end_items: list[InteractionItem] = []

    def send(
        self,
        blocks: ContentBlock | Mapping[str, Any] | str | list[ContentBlock | Mapping[str, Any] | str],
        *,
        kind: str = "message",
        write_history: bool | None = None,
        history_policy: str | None = None,
        visible: bool = True,
        history_text: str | None = None,
        failure_history_text: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> DeliveryHandle:
        """Commit a host-mediated delivery request."""

        resolved_policy = self._resolve_history_policy(write_history, history_policy, visible=visible)
        normalized = self._normalize_blocks(blocks)
        request_metadata = dict(metadata or {})
        request_metadata["delivery"] = "immediate"
        if self.background:
            request_metadata["background"] = True
        if self.route is not None:
            request_metadata.setdefault("route", self._route_metadata())
        request = DeliveryRequest(
            delivery_id=utc_id("delivery_"),
            kind=kind,
            blocks=normalized,
            history_policy=resolved_policy,
            delivery="immediate",
            visible=visible,
            target="current",
            history_text=history_text,
            failure_history_text=failure_history_text,
            metadata=request_metadata,
        )
        item = self.commit(request)
        if item is not None:
            self.items.append(item)
            self.schedule(item)
        return DeliveryHandle(delivery_id=request.delivery_id)

    def _resolve_history_policy(
        self,
        write_history: bool | None,
        history_policy: str | None,
        *,
        visible: bool,
    ) -> str:
        if history_policy is not None:
            resolved = history_policy
            resolved_write = history_policy != "transient"
        else:
            resolved_write = self.default_write_history if write_history is None else write_history
            if resolved_write is None:
                resolved_write = self.default_history_policy != "transient"
            resolved = "persist" if resolved_write else "transient"
        if resolved not in {"persist", "model_hidden", "transient"}:
            raise ValueError(f"invalid history policy: {resolved}")
        if resolved_write and not self.allow_write_history:
            raise RuntimeError("parallel output modules cannot write session history")
        if not visible and not resolved_write:
            raise ValueError("send with visible=False and write_history=False has no effect")
        return resolved

    def flush_slot_end(self) -> None:
        for item in self.slot_end_items:
            self.schedule(item)
        self.slot_end_items.clear()

    def send_text(
        self,
        text: str,
        *,
        write_history: bool | None = None,
        history_policy: str | None = None,
        visible: bool = True,
        history_text: str | None = None,
        failure_history_text: str | None = None,
        delivery_metadata: Mapping[str, Any] | None = None,
    ) -> DeliveryHandle:
        return self.send(
            ContentBlock(type="text", text=text),
            write_history=write_history,
            history_policy=history_policy,
            visible=visible,
            history_text=history_text if history_text is not None else text,
            failure_history_text=failure_history_text,
            metadata=delivery_metadata,
        )

    def progress(
        self,
        text: str,
        *,
        visible: bool = True,
        delivery_metadata: Mapping[str, Any] | None = None,
    ) -> DeliveryHandle:
        """Emit transient immediate progress for long-running modules."""

        return self.send(
            ContentBlock(type="text", text=text),
            kind="progress",
            write_history=False,
            visible=visible,
            metadata=delivery_metadata,
        )

    def notice(
        self,
        text: str,
        *,
        visible: bool = True,
        delivery_metadata: Mapping[str, Any] | None = None,
    ) -> DeliveryHandle:
        """Emit a transient immediate notice for user-visible module status."""

        return self.send(
            ContentBlock(type="text", text=text),
            kind="notice",
            write_history=False,
            visible=visible,
            metadata=delivery_metadata,
        )

    def send_image(
        self,
        source: ArtifactRef | str | Path,
        *,
        caption: str | None = None,
        media_type: str | None = None,
        summary: str | None = None,
        artifact_metadata: Mapping[str, Any] | None = None,
        write_history: bool | None = None,
        history_policy: str | None = None,
        visible: bool = True,
        history_text: str | None = None,
        failure_history_text: str | None = None,
        delivery_metadata: Mapping[str, Any] | None = None,
    ) -> DeliveryHandle:
        return self._send_artifact_block(
            "image",
            source,
            caption=caption,
            media_type=media_type,
            summary=summary,
            artifact_metadata=artifact_metadata,
            write_history=write_history,
            history_policy=history_policy,
            visible=visible,
            history_text=history_text,
            failure_history_text=failure_history_text,
            delivery_metadata=delivery_metadata,
        )

    def send_audio(
        self,
        source: ArtifactRef | str | Path,
        *,
        caption: str | None = None,
        media_type: str | None = None,
        summary: str | None = None,
        artifact_metadata: Mapping[str, Any] | None = None,
        write_history: bool | None = None,
        history_policy: str | None = None,
        visible: bool = True,
        history_text: str | None = None,
        failure_history_text: str | None = None,
        delivery_metadata: Mapping[str, Any] | None = None,
    ) -> DeliveryHandle:
        return self._send_artifact_block(
            "audio",
            source,
            caption=caption,
            media_type=media_type,
            summary=summary,
            artifact_metadata=artifact_metadata,
            write_history=write_history,
            history_policy=history_policy,
            visible=visible,
            history_text=history_text,
            failure_history_text=failure_history_text,
            delivery_metadata=delivery_metadata,
        )

    def send_video(
        self,
        source: ArtifactRef | str | Path,
        *,
        caption: str | None = None,
        media_type: str | None = None,
        summary: str | None = None,
        artifact_metadata: Mapping[str, Any] | None = None,
        write_history: bool | None = None,
        history_policy: str | None = None,
        visible: bool = True,
        history_text: str | None = None,
        failure_history_text: str | None = None,
        delivery_metadata: Mapping[str, Any] | None = None,
    ) -> DeliveryHandle:
        return self._send_artifact_block(
            "video",
            source,
            caption=caption,
            media_type=media_type,
            summary=summary,
            artifact_metadata=artifact_metadata,
            write_history=write_history,
            history_policy=history_policy,
            visible=visible,
            history_text=history_text,
            failure_history_text=failure_history_text,
            delivery_metadata=delivery_metadata,
        )

    def send_file(
        self,
        source: ArtifactRef | str | Path,
        *,
        caption: str | None = None,
        media_type: str | None = None,
        summary: str | None = None,
        artifact_metadata: Mapping[str, Any] | None = None,
        write_history: bool | None = None,
        history_policy: str | None = None,
        visible: bool = True,
        history_text: str | None = None,
        failure_history_text: str | None = None,
        delivery_metadata: Mapping[str, Any] | None = None,
    ) -> DeliveryHandle:
        return self._send_artifact_block(
            "file",
            source,
            caption=caption,
            media_type=media_type,
            summary=summary,
            artifact_metadata=artifact_metadata,
            write_history=write_history,
            history_policy=history_policy,
            visible=visible,
            history_text=history_text,
            failure_history_text=failure_history_text,
            delivery_metadata=delivery_metadata,
        )

    def _send_artifact_block(
        self,
        block_type: str,
        source: ArtifactRef | str | Path,
        *,
        caption: str | None,
        media_type: str | None,
        summary: str | None,
        artifact_metadata: Mapping[str, Any] | None,
        write_history: bool | None,
        history_policy: str | None,
        visible: bool,
        history_text: str | None,
        failure_history_text: str | None,
        delivery_metadata: Mapping[str, Any] | None,
    ) -> DeliveryHandle:
        artifact_input = self._coerce_artifact(
            source,
            kind=block_type,
            media_type=media_type,
            summary=summary,
            artifact_metadata=artifact_metadata,
        )
        blocks = [ContentBlock(type=block_type, text=caption, artifact=artifact_input)]
        return self.send(
            blocks,
            write_history=write_history,
            history_policy=history_policy,
            visible=visible,
            history_text=history_text,
            failure_history_text=failure_history_text,
            metadata=delivery_metadata,
        )

    def _route_metadata(self) -> dict[str, Any]:
        if self.route is None:
            return {}
        return {
            "session_id": self.route.session_id,
            "turn_id": self.route.turn_id,
            "channel": self.route.channel,
            "conversation_key": self.route.conversation_key,
            "source": self.route.source,
            "reply_to": self.route.reply_to,
            "slot": self.route.slot,
        }

    def _coerce_artifact(
        self,
        value: ArtifactRef | str | Path,
        *,
        kind: str,
        media_type: str | None = None,
        summary: str | None = None,
        artifact_metadata: Mapping[str, Any] | None = None,
    ) -> ArtifactInput | ArtifactRef:
        if isinstance(value, ArtifactRef):
            if any(item is not None for item in (media_type, summary, artifact_metadata)):
                return ArtifactRef(
                    artifact_id=value.artifact_id,
                    kind=value.kind,
                    media_type=media_type or value.media_type,
                    path=value.path,
                    url=value.url,
                    summary=summary or value.summary,
                    metadata={**dict(value.metadata), **dict(artifact_metadata or {})},
                )
            return value
        if isinstance(value, (ArtifactInput, Mapping)):
            raise TypeError(
                f"send_{kind} source must be a path, URL, or ArtifactRef; "
                "pass summary=... and artifact_metadata=... instead of an artifact dict"
            )
        if not isinstance(value, (str, Path)):
            raise TypeError(f"send_{kind} source must be a path, URL, or ArtifactRef")
        text = str(value)
        if is_url(text):
            return ArtifactInput(
                kind=kind,
                media_type=media_type,
                url=text,
                summary=summary,
                metadata=dict(artifact_metadata or {}),
            )
        return self._artifact_from_path(
            text,
            kind=kind,
            media_type=media_type,
            summary=summary,
            metadata=artifact_metadata,
        )

    def _artifact_from_path(
        self,
        path: str | Path,
        *,
        kind: str,
        media_type: str | None = None,
        summary: str | None = None,
        filename: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> ArtifactInput:
        raw_path = Path(path)
        resolved = raw_path if raw_path.is_absolute() else self.workspace / raw_path
        resolved = resolved.resolve()
        if not (
            is_relative_to(resolved, self.workspace)
            or is_relative_to(resolved, self.session_root)
        ):
            raise ValueError(f"attachment path is outside the workspace or session: {path}")
        return ArtifactInput(
            kind=kind,
            media_type=media_type,
            path=str(resolved),
            summary=summary,
            filename=filename or resolved.name,
            metadata=dict(metadata or {}),
        )

    def _normalize_blocks(
        self,
        value: ContentBlock | Mapping[str, Any] | str | list[ContentBlock | Mapping[str, Any] | str],
    ) -> list[ContentBlock]:
        raw_blocks = value if isinstance(value, list) else [value]
        blocks: list[ContentBlock] = []
        for raw in raw_blocks:
            if isinstance(raw, ContentBlock):
                block = raw
            elif isinstance(raw, str):
                block = ContentBlock(type="text", text=raw)
            elif isinstance(raw, Mapping):
                block = ContentBlock(**dict(raw))
            else:
                raise TypeError(f"invalid delivery block: {type(raw)!r}")
            if block.type not in CONTENT_BLOCK_TYPES:
                raise ValueError(f"invalid delivery block type: {block.type}")
            blocks.append(block)
        return blocks


class RuntimeIO:
    """Runner-owned send pipeline for transcript and channel items."""

    def __init__(self, runner: "SessionTurnStepRunner"):
        self.runner = runner

    def send_user(
        self,
        *,
        turn_id: str,
        content: str,
        interaction_metadata: dict[str, Any],
    ) -> SessionMessage | None:
        if not content:
            return None
        message = self.runner.session_runtime.append_message(
            self.runner.session_id,
            role="user",
            content=content,
            turn_id=turn_id,
            interaction_metadata=interaction_metadata,
        )
        self.runner.event_log.emit(
            "message.persisted",
            turn_id=turn_id,
            message_id=message.id,
            role=message.role,
            kind=message.kind,
            **interaction_metadata,
        )
        return message

    async def send_assistant_step(
        self,
        *,
        turn: TurnContext,
        step_id: str,
        content: str,
        tool_calls: list[ToolCall],
        interaction_metadata: dict[str, Any],
    ) -> tuple[SessionMessage, list[InteractionItem]]:
        message = self.runner.session_runtime.append_message(
            self.runner.session_id,
            role="assistant",
            content=content,
            turn_id=turn.turn_id,
            visible=bool(content.strip()),
            model_visible=True,
            interaction_metadata=interaction_metadata,
            metadata={
                "phase": "model_step",
                "step_id": step_id,
                "interim": bool(content.strip()),
                "tool_calls": [asdict(call) for call in tool_calls],
            },
        )
        self.runner.event_log.emit(
            "message.persisted",
            turn_id=turn.turn_id,
            step_id=step_id,
            message_id=message.id,
            role=message.role,
            kind=message.kind,
            tool_calls=[asdict(call) for call in tool_calls],
            **interaction_metadata,
        )
        item = self._assistant_interim_item(
            content,
            turn=turn,
            step_id=step_id,
            message_id=message.id,
            interaction_metadata=interaction_metadata,
        )
        if item is None:
            return message, []
        self.runner._schedule_interaction_item(
            item,
            turn=turn,
            interaction_metadata=interaction_metadata,
        )
        return message, [item]

    def send_tool_result(
        self,
        *,
        turn: TurnContext,
        step_id: str,
        record: ToolExecutionRecord,
        interaction_metadata: dict[str, Any],
    ) -> InteractionItem:
        message = self._persist_tool_result_message(
            turn=turn,
            step_id=step_id,
            record=record,
            interaction_metadata=interaction_metadata,
        )
        item = InteractionItem.tool_result_item(
            record,
            metadata={
                "phase": "model_step",
                "step_id": step_id,
                "message_id": message.id,
                "tool_name": record.call.name,
                "tool_call_id": record.call.id,
                "is_error": record.result.is_error,
            },
        )
        self.runner._schedule_interaction_item(
            item,
            turn=turn,
            interaction_metadata=interaction_metadata,
        )
        return item

    async def send_tool_call_started(
        self,
        *,
        turn: TurnContext,
        step_id: str,
        call: ToolCall,
        interaction_metadata: dict[str, Any],
    ) -> InteractionItem:
        record = ToolInteractionRecord.started(
            call,
            metadata={
                "phase": "model_step",
                "step_id": step_id,
                "tool_name": call.name,
                "tool_call_id": call.id,
                "tool_phase": "start",
            },
        )
        item = InteractionItem.tool_call_item(record)
        await self.runner._dispatch_interaction_item_now(
            item,
            turn=turn,
            interaction_metadata=interaction_metadata,
        )
        return item

    async def send_tool_call_finished(
        self,
        *,
        turn: TurnContext,
        step_id: str,
        record: ToolExecutionRecord,
        interaction_metadata: dict[str, Any],
    ) -> InteractionItem:
        message = self._persist_tool_result_message(
            turn=turn,
            step_id=step_id,
            record=record,
            interaction_metadata=interaction_metadata,
        )
        tool_record = ToolInteractionRecord.finished(
            record,
            metadata={
                "phase": "model_step",
                "step_id": step_id,
                "message_id": message.id,
                "tool_name": record.call.name,
                "tool_call_id": record.call.id,
                "tool_phase": "finish",
                "is_error": record.result.is_error,
            },
        )
        item = InteractionItem.tool_call_item(tool_record)
        await self.runner._dispatch_interaction_item_now(
            item,
            turn=turn,
            interaction_metadata=interaction_metadata,
        )
        return item

    def _persist_tool_result_message(
        self,
        *,
        turn: TurnContext,
        step_id: str,
        record: ToolExecutionRecord,
        interaction_metadata: dict[str, Any],
    ) -> SessionMessage:
        content = self.runner._truncate_model_content(self.runner._tool_result_model_content(record.result))
        message = self.runner.session_runtime.append_message(
            self.runner.session_id,
            role="tool",
            content=content,
            turn_id=turn.turn_id,
            visible=False,
            model_visible=True,
            interaction_metadata=interaction_metadata,
            metadata={
                "phase": "model_step",
                "step_id": step_id,
                "tool_name": record.call.name,
                "tool_call_id": record.call.id,
                "is_error": record.result.is_error,
            },
        )
        self.runner.event_log.emit(
            "message.persisted",
            turn_id=turn.turn_id,
            step_id=step_id,
            message_id=message.id,
            role=message.role,
            kind=message.kind,
            tool_name=record.call.name,
            **interaction_metadata,
        )
        return message

    def send_module_output(
        self,
        request: DeliveryRequest,
        *,
        turn: TurnContext,
        slot: SlotDefinition,
        interaction_metadata: dict[str, Any],
    ) -> InteractionItem | None:
        delivery = self.runner._apply_delivery_request(
            request,
            turn=turn,
            slot=slot,
            interaction_metadata=interaction_metadata,
        )
        return InteractionItem.delivery_item(delivery) if delivery is not None else None

    def _assistant_interim_item(
        self,
        content: str,
        *,
        turn: TurnContext,
        step_id: str,
        message_id: str,
        interaction_metadata: dict[str, Any],
    ) -> InteractionItem | None:
        text = content.strip()
        if not text:
            return None
        metadata = {
            "phase": "model_step",
            "step_id": step_id,
            "message_id": message_id,
            "interim": True,
            "history_policy": "persist",
            "delivery": "immediate",
            "delivery_status": "pending",
        }
        delivery = InteractionDelivery(
            type="text",
            kind="message",
            text=text,
            fallback_text=text,
            blocks=[{"type": "text", "text": text, "metadata": {"interim": True, "step_id": step_id}}],
            payload={"type": "text", "text": text},
            visible=True,
            history_policy="persist",
            metadata=metadata,
        )
        self.runner.event_log.emit(
            "message.interim",
            turn_id=turn.turn_id,
            step_id=step_id,
            message_id=message_id,
            content=text,
            **interaction_metadata,
        )
        return InteractionItem.delivery_item(delivery)


class ModuleResultClient:
    """Structured result SDK exposed to authored output modules."""

    _ARTIFACT_HINT_KEYS = {"artifact_id", "kind", "media_type", "summary", "filename"}

    def __init__(
        self,
        *,
        home: Path,
        session_id: str,
        workspace: str | None,
        writable: bool = True,
        state: dict[str, Any] | None = None,
    ):
        self.home = home
        self.session_id = session_id
        self.workspace = Path(workspace or ".").resolve()
        self.session_root = (home / "runtime" / "artifacts" / session_id).resolve()
        self.writable = writable
        self._state = state if state is not None else {"is_set": False, "value": None}

    @property
    def value(self) -> Any:
        return self._state["value"] if self._state["is_set"] else None

    def fork(self, *, writable: bool) -> "ModuleResultClient":
        return ModuleResultClient(
            home=self.home,
            session_id=self.session_id,
            workspace=str(self.workspace),
            writable=writable,
            state=self._state,
        )

    def set(self, value: Any) -> Any:
        if not self.writable:
            raise RuntimeError("parallel output modules cannot modify the current agent result")
        normalized = self._normalize_value(value)
        if isinstance(normalized, dict) and isinstance(self._state["value"], dict):
            self._state["value"] = {**self._state["value"], **normalized}
        else:
            self._state["value"] = normalized
        self._state["is_set"] = True
        return self._state["value"]

    def _normalize_value(self, value: Any) -> Any:
        if isinstance(value, (ArtifactInput, ArtifactRef)):
            return self._normalize_value(artifact_input_to_dict(value))
        if value is None or isinstance(value, (str, bool, int)):
            return value
        if isinstance(value, float):
            if not math.isfinite(value):
                raise ValueError("ctx.result values must be finite JSON numbers")
            return value
        if isinstance(value, (list, tuple)):
            return [self._normalize_value(item) for item in value]
        if isinstance(value, Mapping):
            normalized: dict[str, Any] = {}
            for key, item in value.items():
                if not isinstance(key, str):
                    raise ValueError("ctx.result object keys must be strings")
                normalized[key] = self._normalize_value(item)
            self._validate_artifact_descriptor(normalized)
            return normalized
        raise TypeError(f"ctx.result value must be JSON-compatible, got {type(value).__name__}")

    def _validate_artifact_descriptor(self, value: dict[str, Any]) -> None:
        has_artifact_location = any(key in value for key in ("path", "url", "content"))
        if not has_artifact_location or not (self._ARTIFACT_HINT_KEYS & set(value)):
            return
        metadata = value.get("metadata")
        if metadata is not None and not isinstance(metadata, dict):
            raise ValueError("result artifact metadata must be an object")
        path = value.get("path")
        if path is None:
            return
        raw_path = Path(str(path))
        resolved = raw_path if raw_path.is_absolute() else self.workspace / raw_path
        resolved = resolved.resolve()
        if not (
            is_relative_to(resolved, self.workspace)
            or is_relative_to(resolved, self.session_root)
        ):
            raise ValueError(f"result artifact path is outside the workspace or session: {path}")
        value["path"] = str(resolved)


class SessionTurnStepRunner:
    def __init__(
        self,
        *,
        home: Path,
        version_store: VersionStore,
        core_loader: CoreLoader,
        provider: Provider,
        tool_runtime: ToolRuntime,
        core_id: str = "assistant",
        session_id: str | None = None,
        model_override: str | None = None,
        model_resolver: Callable[[Any], str] | None = None,
        provider_name: str | None = None,
        workspace: str | None = None,
        initial_core_path: Path | None = None,
        show_system_prompt: bool = False,
        runtime_timezone: RuntimeTimezone | None = None,
        task_worker: RuntimeTaskWorker | None = None,
        session_runtime: SessionRuntime | None = None,
        slot_runtime: SlotRuntime | None = None,
        turn_engine: TurnEngine | None = None,
        interaction_router: SessionInteractionRouter | None = None,
        prepare_live_core: Callable[[], Awaitable[Any]] | None = None,
    ):
        self.home = home
        self.version_store = version_store
        self.core_loader = core_loader
        self.provider = provider
        self.tool_runtime = tool_runtime
        self.core_id = core_id
        self.session_id = session_id or utc_id("session_")
        self.model_override = model_override
        self.model_resolver = model_resolver
        self.provider_name = provider_name
        self.workspace = workspace
        self.initial_core_path = initial_core_path
        self.show_system_prompt = show_system_prompt
        self.runtime_timezone = runtime_timezone or resolve_runtime_timezone()
        self.task_worker = task_worker or getattr(tool_runtime, "task_worker", None)
        if self.task_worker is None:
            raise ValueError("SessionTurnStepRunner requires a RuntimeControlPlane-backed RuntimeTaskWorker")
        if session_runtime is None:
            raise ValueError("SessionTurnStepRunner requires a RuntimeControlPlane-backed SessionRuntime")
        self.session_runtime = session_runtime
        self.sessions = session_runtime
        self.slot_runtime = slot_runtime or SlotRuntime()
        self.slot_pipeline = SlotPipelineRuntime(RunnerSlotPipelineHost(self))
        self.turn_engine = turn_engine or TurnEngine(RunnerTurnEngineHost(self))
        self.interaction_router = interaction_router or SessionInteractionRouter()
        self.prepare_live_core_callback = prepare_live_core
        self.context_assembler = ContextAssembler()
        self.event_log = EventLog(home, self.session_id)
        self.delivery_runtime = DeliveryRuntime(
            store=self.session_runtime.store,
            event_log=self.event_log,
            router=self.interaction_router,
        )
        self.runtime_io = RuntimeIO(self)
        self.history: list[LLMMessage] = []
        self.display_turns: list[dict[str, Any]] = []
        self._session_started_ids: set[str] = set()
        self._background_tasks: set[asyncio.Task[Any]] = set()
        self._ensure_current_session()

    def emit_turn_event(self, event_type: str, **payload: Any) -> dict[str, Any]:
        return self.event_log.emit(event_type, **payload)

    def emit_slot_event(self, event_type: str, **payload: Any) -> dict[str, Any]:
        return self.event_log.emit(event_type, **payload)

    def track_slot_background_task(self, task: asyncio.Task[Any]) -> None:
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def run_turn(
        self,
        text: str,
        *,
        core_path: Path | None = None,
        interaction: InteractionInbound | None = None,
        injected_system_context: list[str] | None = None,
        input_slot_ids: list[str] | tuple[str, ...] | None = None,
        output_slot_ids: list[str] | tuple[str, ...] | None = None,
        input_phase_slots: ResolvedPhaseSlots | None = None,
        output_phase_slots: ResolvedPhaseSlots | None = None,
        use_bootstrap: bool = True,
        route_binding: SessionRouteBinding | None = None,
    ) -> TurnResult:
        core = self.core_loader.load(core_path) if core_path is not None else await self.load_active_core()
        interaction_metadata = self._interaction_metadata(interaction)
        self._resolve_session_for_interaction(core, interaction_metadata)
        if route_binding is not None:
            route_binding.bind(self.interaction_router, self.session_id)
        if core_path is None:
            self.session_runtime.update_session(
                self.session_id,
                core_id=core.core_id,
                core_revision=self._core_revision(core),
                provider=self.provider_name,
                model=self._resolve_model_name(core),
                touch=False,
            )
        if input_slot_ids is not None and input_phase_slots is not None:
            raise ValueError("input_slot_ids and input_phase_slots cannot both be set")
        if output_slot_ids is not None and output_phase_slots is not None:
            raise ValueError("output_slot_ids and output_phase_slots cannot both be set")
        input_slots_override = self._resolve_phase_slots(core, "input", input_slot_ids)
        output_slots_override = self._resolve_phase_slots(core, "output", output_slot_ids)
        capability = CapabilityFacade(core)
        if not self._session_started:
            self.event_log.emit("session.started", core_id=core.core_id, core_revision=self._core_revision(core), **interaction_metadata)
            self._session_started_ids.add(self.session_id)
        if use_bootstrap:
            await self._ensure_bootstrap_context(core, capability, interaction_metadata=interaction_metadata)

        turn_id = utc_id("turn_")
        input_envelope = InputEnvelope(
            raw_text=text,
            metadata=interaction_metadata,
            attachments=list(interaction.attachments) if interaction is not None else [],
        )
        user_input = AgentInput(content=text, metadata=interaction_metadata)
        state_stores = ModuleStateStores(
            core=StateStore.core(self.home, core.core_id),
            session=StateStore.session(self.home, core_id=core.core_id, session_id=self.session_id),
        )
        turn = TurnContext(
            session_id=self.session_id,
            turn_id=turn_id,
            core_id=core.core_id,
            core_revision=self._core_revision(core),
            user_input=user_input,
            metadata=interaction_metadata,
        )

        self.event_log.emit(
            "turn.started",
            turn_id=turn_id,
            core_id=core.core_id,
            core_revision=self._core_revision(core),
            **interaction_metadata,
        )
        turn_task_id = self._submit_turn_task(core=core, turn_id=turn_id, metadata=interaction_metadata)
        self.session_runtime.start_turn(session_id=self.session_id, turn_id=turn_id, task_id=turn_task_id)
        self.event_log.emit("message.inbound", turn_id=turn_id, content=text, **interaction_metadata)

        try:
            user_text, persisted_user_text, context, input_items = await self._run_input_slots(
                core,
                turn,
                capability,
                input_envelope,
                state_stores,
                interaction_metadata=interaction_metadata,
                injected_system_context=injected_system_context or [],
                serial_slots=input_slots_override,
                phase_slots=input_phase_slots,
            )
        except asyncio.CancelledError:
            self._finalize_interrupted_turn(turn_id, status="cancelled", error="turn cancelled", metadata=interaction_metadata)
            raise
        except Exception as exc:
            self._finalize_interrupted_turn(
                turn_id,
                status="failed",
                error=self._sanitize_runtime_error(exc),
                metadata=interaction_metadata,
            )
            raise
        turn.user_input = AgentInput(content=user_text, metadata=interaction_metadata)
        self.event_log.emit("message.received", turn_id=turn_id, content=user_text, **interaction_metadata)
        if persisted_user_text:
            self.runtime_io.send_user(
                turn_id=turn_id,
                content=persisted_user_text,
                interaction_metadata=interaction_metadata,
            )
        items: list[InteractionItem] = list(input_items)
        await self.tool_runtime.prepare_for_turn(core, turn, emit_event=self.event_log.emit)
        available_tools = self.tool_runtime.definitions_for(core, turn=turn)
        try:
            engine_result = await self.turn_engine.run(
                TurnEngineRequest(
                    core=core,
                    turn=turn,
                    capability=capability,
                    context=context,
                    available_tools=available_tools,
                    interaction_metadata=interaction_metadata,
                    use_bootstrap_context=use_bootstrap,
                )
            )
        except asyncio.CancelledError:
            self._finalize_interrupted_turn(turn_id, status="cancelled", error="turn cancelled", metadata=interaction_metadata)
            raise
        except Exception as exc:
            self._finalize_interrupted_turn(
                turn_id,
                status="failed",
                error=self._sanitize_runtime_error(exc),
                metadata=interaction_metadata,
            )
            raise
        final_output = engine_result.final_output
        needs_user = engine_result.needs_user
        tool_records = engine_result.tool_records
        turn_messages = engine_result.turn_messages
        items.extend(engine_result.items)

        result_client = self._module_result_client(writable=True)
        try:
            output_items = await self._run_output_slots(
                core,
                turn,
                capability,
                current_output=final_output,
                tool_records=tool_records,
                state_stores=state_stores,
                interaction_metadata=interaction_metadata,
                result_client=result_client,
                serial_slots=output_slots_override,
                phase_slots=output_phase_slots,
            )
        except asyncio.CancelledError:
            self._finalize_interrupted_turn(turn_id, status="cancelled", error="turn cancelled", metadata=interaction_metadata)
            raise
        except Exception as exc:
            self._finalize_interrupted_turn(
                turn_id,
                status="failed",
                error=self._sanitize_runtime_error(exc),
                metadata=interaction_metadata,
            )
            raise
        items.extend(output_items)
        delivered_texts = [
            item.delivery.text
            for item in items
            if item.kind == "delivery" and item.delivery is not None and item.delivery.visible and item.delivery.text
        ]
        for text in delivered_texts:
            turn_messages.append(LLMMessage(role="assistant", content=text))
        self.history = self._session_history_messages()
        self.display_turns.append(
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
        self.event_log.emit(
            "turn.completed",
            turn_id=turn_id,
            items=[asdict(item) for item in items],
            agent_result=result_client.value,
            needs_user=needs_user,
            **interaction_metadata,
        )
        self._complete_turn_task(turn_id, result_ref=turn_id)
        self.session_runtime.complete_turn(session_id=self.session_id, turn_id=turn_id, result_ref=turn_id)
        self._ack_background_completion_claims(interaction_metadata)
        return TurnResult(
            session_id=self.session_id,
            turn_id=turn_id,
            core_id=core.core_id,
            core_revision=self._core_revision(core),
            items=items,
            agent_result=result_client.value,
            needs_user=needs_user,
        )

    @property
    def _session_started(self) -> bool:
        return self.session_id in self._session_started_ids

    async def _ensure_bootstrap_context(
        self,
        core: LoadedCore,
        capability: CapabilityFacade,
        *,
        interaction_metadata: dict[str, Any],
    ) -> None:
        if not core.bootstrap_enabled:
            return
        if self.sessions.bootstrap_context_exists(self.session_id):
            return

        self.event_log.emit(
            "bootstrap.started",
            core_id=core.core_id,
            core_revision=self._core_revision(core),
            slots=[slot.slot_id for slot in core.bootstrap_pipeline.serial],
            **interaction_metadata,
        )
        fragments: list[str] = []
        try:
            for slot in core.bootstrap_pipeline.serial:
                self.event_log.emit(
                    "bootstrap.module.started",
                    core_id=core.core_id,
                    core_revision=self._core_revision(core),
                    slot=slot.relative_path,
                    kind="bootstrap",
                    **interaction_metadata,
                )
                client = ModuleBootstrapClient(workspace=self.workspace)
                ctx = BootstrapContext(
                    session_id=self.session_id,
                    core_id=core.core_id,
                    core_revision=self._core_revision(core),
                    workspace=self.workspace or "",
                    slot_id=slot.slot_id,
                    slot_path=slot.relative_path,
                    capability=capability,
                    bootstrap=client,
                )
                try:
                    value = await self._call_slot(slot, ctx)
                    if value is not None:
                        self.event_log.emit(
                            "bootstrap.module.return_ignored",
                            core_id=core.core_id,
                            core_revision=self._core_revision(core),
                            slot=slot.relative_path,
                            kind="bootstrap",
                            **interaction_metadata,
                        )
                    fragments.extend(client.fragments)
                    self.event_log.emit(
                        "bootstrap.module.completed",
                        core_id=core.core_id,
                        core_revision=self._core_revision(core),
                        slot=slot.relative_path,
                        kind="bootstrap",
                        fragments=len(client.fragments),
                        chars=sum(len(fragment) for fragment in client.fragments),
                        **interaction_metadata,
                    )
                except Exception as exc:
                    self.event_log.emit(
                        "bootstrap.module.failed",
                        core_id=core.core_id,
                        core_revision=self._core_revision(core),
                        slot=slot.relative_path,
                        kind="bootstrap",
                        error=str(exc),
                        **interaction_metadata,
                    )
                    if slot.failure_policy == "hard":
                        raise
            content = "\n\n".join(fragments)
            self.sessions.write_bootstrap_context(self.session_id, content)
            self.event_log.emit(
                "bootstrap.completed",
                core_id=core.core_id,
                core_revision=self._core_revision(core),
                fragments=len(fragments),
                chars=len(content),
                **interaction_metadata,
            )
        except Exception as exc:
            self.event_log.emit(
                "bootstrap.failed",
                core_id=core.core_id,
                core_revision=self._core_revision(core),
                error=str(exc),
                **interaction_metadata,
            )
            raise

    def _build_messages(
        self,
        core: LoadedCore,
        context: list[ContextContribution],
        turn_messages: list[LLMMessage],
        *,
        turn_id: str,
        step_id: str,
        use_bootstrap_context: bool = True,
    ) -> list[LLMMessage]:
        assembled = self.context_assembler.assemble(
            core=core,
            context=context,
            session_history=[
                message
                for message in self.sessions.history_for_context(self.session_id)
                if message.turn_id != turn_id
            ],
            current_turn_messages=turn_messages,
            bootstrap_context=self.sessions.read_bootstrap_context(self.session_id) if use_bootstrap_context else None,
            compaction_summary=self.sessions.latest_compaction_summary(self.session_id),
        )
        self.event_log.emit(
            "context.assembled",
            turn_id=turn_id,
            step_id=step_id,
            layers=assembled.layer_summaries(),
            total_messages=len(assembled.messages),
            total_chars=sum(len(message.content or "") for message in assembled.messages),
        )
        return assembled.messages

    def build_turn_messages(
        self,
        core: LoadedCore,
        context: list[ContextContribution],
        turn_messages: list[LLMMessage],
        *,
        turn_id: str,
        step_id: str,
        use_bootstrap_context: bool = True,
    ) -> list[LLMMessage]:
        return self._build_messages(
            core,
            context,
            turn_messages,
            turn_id=turn_id,
            step_id=step_id,
            use_bootstrap_context=use_bootstrap_context,
        )

    async def _maybe_deliver_system_prompt_debug(
        self,
        messages: list[LLMMessage],
        *,
        turn: TurnContext,
        step_id: str,
        interaction_metadata: dict[str, Any],
    ) -> None:
        if not self.show_system_prompt:
            return
        system_messages = [
            message
            for message in messages
            if message.role == "system" and (message.content or "").strip()
        ]
        if not system_messages:
            self.event_log.emit(
                "debug.system_prompt.skipped",
                turn_id=turn.turn_id,
                step_id=step_id,
                reason="no_system_messages",
                **interaction_metadata,
            )
            return

        channel = interaction_metadata.get("channel")
        if not channel:
            self.event_log.emit(
                "debug.system_prompt.skipped",
                turn_id=turn.turn_id,
                step_id=step_id,
                reason="no_channel",
                system_messages=len(system_messages),
                total_chars=sum(len(message.content or "") for message in system_messages),
                **interaction_metadata,
            )
            return

        text = self._format_system_prompt_debug(system_messages, turn_id=turn.turn_id, step_id=step_id)
        metadata = {
            "role": "system",
            "debug": "system_prompt",
            "level": "info",
            "history_policy": "transient",
            "delivery": "immediate",
            "delivery_status": "pending",
            "system_messages": len(system_messages),
        }
        delivery = InteractionDelivery(
            type="text",
            kind="notice",
            text=text,
            fallback_text=text,
            blocks=[{"type": "text", "text": text, "metadata": {"debug": "system_prompt"}}],
            payload={"type": "text", "text": text},
            visible=True,
            history_policy="transient",
            metadata=metadata,
        )
        item = InteractionItem.delivery_item(delivery)
        outbound = InteractionOutbound(
            channel=str(channel),
            items=[item],
            session_id=self.session_id,
            turn_id=turn.turn_id,
            metadata=dict(interaction_metadata),
        )
        try:
            result = await self.interaction_router.deliver(outbound)
            self.event_log.emit(
                "debug.system_prompt.unrouted" if result.status == "unrouted" else "debug.system_prompt.delivered",
                turn_id=turn.turn_id,
                step_id=step_id,
                system_messages=len(system_messages),
                total_chars=sum(len(message.content or "") for message in system_messages),
                **interaction_metadata,
            )
        except Exception as exc:
            item.set_dispatch_status("failed")
            self.event_log.emit(
                "debug.system_prompt.failed",
                turn_id=turn.turn_id,
                step_id=step_id,
                error=str(exc),
                system_messages=len(system_messages),
                total_chars=sum(len(message.content or "") for message in system_messages),
                **interaction_metadata,
            )

    async def deliver_turn_system_prompt_debug(
        self,
        messages: list[LLMMessage],
        *,
        turn: TurnContext,
        step_id: str,
        interaction_metadata: dict[str, Any],
    ) -> None:
        await self._maybe_deliver_system_prompt_debug(
            messages,
            turn=turn,
            step_id=step_id,
            interaction_metadata=interaction_metadata,
        )

    def _format_system_prompt_debug(self, messages: list[LLMMessage], *, turn_id: str, step_id: str) -> str:
        sections = [
            "# System prompt debug",
            "",
            f"turn: {turn_id}",
            f"step: {step_id}",
        ]
        sections.extend(
            [
                "",
                "## Final system prompt",
                "",
                "\n\n".join(message.content or "" for message in messages),
            ]
        )
        return "\n".join(sections).strip()

    def _build_skill_index(self, core: LoadedCore) -> str:
        return self.context_assembler._build_skill_index(core)

    def _core_revision(self, core: LoadedCore) -> str:
        try:
            return self.version_store.active_pointer(core.core_id).active_revision
        except Exception:
            return "untracked"

    def _resolve_model_name(self, core: LoadedCore) -> str:
        if self.model_resolver:
            return self.model_resolver(core.manifest.model)
        if self.model_override:
            return self.model_override
        return core.manifest.model.model_name or "fake/demo"

    def resolve_turn_model_name(self, core: LoadedCore) -> str:
        return self._resolve_model_name(core)

    async def complete_turn_provider(self, request: LLMRequest) -> LLMResponse:
        return await self.provider.complete(request)

    async def prepare_live_core(self) -> None:
        if self.prepare_live_core_callback is not None:
            await self.prepare_live_core_callback()

    async def load_active_core(self) -> LoadedCore:
        await self.prepare_live_core()
        return self.core_loader.load(self.version_store.active_core_path(self.core_id))

    def start_new_session(
        self,
        *,
        channel: str | None = None,
        conversation_key: str | None = None,
        source: str | None = None,
        reply_to: str | None = None,
        replace_conversation_binding: bool = False,
    ) -> str:
        core = self.core_loader.load(self.version_store.active_core_path(self.core_id))
        core_revision = self._core_revision(core)
        metadata = {key: value for key, value in {"source": source, "reply_to": reply_to}.items() if value is not None}
        bind_immediately = not (replace_conversation_binding and channel and conversation_key)
        record = self.session_runtime.create_session(
            core_id=core.core_id,
            core_revision=core_revision,
            channel=channel if bind_immediately else None,
            conversation_key=conversation_key if bind_immediately else None,
            workspace=self.workspace,
            provider=self.provider_name,
            model=self._resolve_model_name(core),
            metadata=metadata,
        )
        if not bind_immediately:
            record = self.session_runtime.rebind_interaction_session(
                record.session_id,
                core_id=core.core_id,
                core_revision=core_revision,
                channel=channel,
                conversation_key=conversation_key,
                metadata=metadata,
            )
        self._switch_session(record.session_id, emit_resumed=False)
        self.event_log.emit(
            "session.created",
            core_id=core.core_id,
            core_revision=core_revision,
            channel=channel,
            conversation_key=conversation_key,
        )
        return record.session_id

    def resume_session(self, session_id: str) -> None:
        self._switch_session(session_id, emit_resumed=True)

    async def compact_session(self, *, focus: str | None = None, protect_last_n: int = 6) -> CompactionResult:
        core = await self.load_active_core()
        turn_id = utc_id("compact_")
        self.event_log.emit("session.compaction.started", turn_id=turn_id, focus=focus)
        try:
            messages = [
                message
                for message in self.sessions.history_for_context(self.session_id)
                if message.kind == "message" and message.turn_id
            ]
            turn_ids = list(dict.fromkeys(message.turn_id for message in messages if message.turn_id))
            protected_turns = max(protect_last_n, 0)
            if len(turn_ids) <= protected_turns:
                result = CompactionResult(
                    session_id=self.session_id,
                    turn_id=turn_id,
                    compacted_count=0,
                    summary_message_id=None,
                    summary="not enough history to compact",
                    skipped=True,
                )
                self.event_log.emit("session.compaction.completed", turn_id=turn_id, skipped=True, compacted_count=0)
                return result

            compact_turn_ids = set(turn_ids[:-protected_turns] if protected_turns else turn_ids)
            to_compact = [message for message in messages if message.turn_id in compact_turn_ids]
            if not to_compact:
                result = CompactionResult(
                    session_id=self.session_id,
                    turn_id=turn_id,
                    compacted_count=0,
                    summary_message_id=None,
                    summary="not enough history to compact",
                    skipped=True,
                )
                self.event_log.emit("session.compaction.completed", turn_id=turn_id, skipped=True, compacted_count=0)
                return result
            transcript = "\n\n".join(self._format_compaction_message(message) for message in to_compact)
            request = LLMRequest(
                model=self._resolve_model_name(core),
                messages=[
                    LLMMessage(
                        role="system",
                        content=(
                            "Summarize prior conversation turns for future context. Preserve durable facts, "
                            "decisions, unresolved questions, files or commands mentioned, and user preferences. "
                            "Write historical reference only; do not create new tasks."
                        ),
                    ),
                    LLMMessage(
                        role="user",
                        content="\n\n".join(
                            part
                            for part in [
                                f"Focus: {focus}" if focus else "",
                                "Transcript to compact:",
                                transcript,
                            ]
                            if part
                        ),
                    ),
                ],
                metadata={"turn_id": turn_id, "kind": "session_compaction"},
            )
            response: LLMResponse = await self.provider.complete(request)
            summary_body = (response.content or "").strip()
            if not summary_body:
                raise ValueError("provider returned an empty compaction summary")
            summary = f"{SUMMARY_PREFIX}\n\n{summary_body}\n\n{SUMMARY_END_MARKER}"
            summary_message = self.session_runtime.write_compaction_summary(
                self.session_id,
                content=summary,
                turn_id=turn_id,
                compacted_until_message_id=to_compact[-1].id,
                compacted_count=len(to_compact),
                focus=focus,
            )
            self.history = self._session_history_messages()
            self.event_log.emit(
                "session.compaction.completed",
                turn_id=turn_id,
                compacted_count=len(to_compact),
                summary_message_id=summary_message.id,
            )
            return CompactionResult(
                session_id=self.session_id,
                turn_id=turn_id,
                compacted_count=len(to_compact),
                summary_message_id=summary_message.id,
                summary=summary,
            )
        except Exception as exc:
            self.event_log.emit("session.compaction.failed", turn_id=turn_id, error=str(exc))
            return CompactionResult(
                session_id=self.session_id,
                turn_id=turn_id,
                compacted_count=0,
                summary_message_id=None,
                summary="",
                error=str(exc),
            )

    def _format_compaction_message(self, message: SessionMessage) -> str:
        metadata = message.metadata or {}
        prefix = message.role.upper()
        if message.role == "assistant" and metadata.get("tool_calls"):
            tool_calls = json.dumps(metadata["tool_calls"], ensure_ascii=False)
            if message.content.strip():
                return f"{prefix} [{message.turn_id} {metadata.get('step_id')}]: {message.content}\nTOOL_CALLS: {tool_calls}"
            return f"{prefix} [{message.turn_id} {metadata.get('step_id')}] TOOL_CALLS: {tool_calls}"
        if message.role == "tool":
            label = metadata.get("tool_name") or "tool"
            call_id = metadata.get("tool_call_id") or ""
            return f"TOOL {label} [{message.turn_id} {metadata.get('step_id')} {call_id}]: {message.content}"
        return f"{prefix} [{message.turn_id}]: {message.content}"

    def _ensure_current_session(self) -> None:
        core = self.core_loader.load(self.initial_core_path or self.version_store.active_core_path(self.core_id))
        _, created = self.session_runtime.ensure_session(
            self.session_id,
            core_id=core.core_id,
            core_revision=self._core_revision(core),
            workspace=self.workspace,
            provider=self.provider_name,
            model=self._resolve_model_name(core),
        )
        self.event_log = EventLog(self.home, self.session_id)
        self.delivery_runtime.event_log = self.event_log
        self.event_log.emit(
            "session.created" if created else "session.resumed",
            core_id=core.core_id,
            core_revision=self._core_revision(core),
        )
        self.history = self._session_history_messages()

    def _switch_session(self, session_id: str, *, emit_resumed: bool) -> None:
        if not self.sessions.exists(session_id):
            raise FileNotFoundError(f"session not found: {session_id}")
        self.session_id = session_id
        self.event_log = EventLog(self.home, self.session_id)
        self.delivery_runtime.event_log = self.event_log
        self.history = self._session_history_messages()
        if emit_resumed:
            record = self.sessions.get_session(session_id)
            self.event_log.emit(
                "session.resumed",
                core_id=record.core_id,
                core_revision=record.core_revision,
                channel=record.channel,
                conversation_key=record.conversation_key,
            )

    def _interaction_metadata(self, interaction: InteractionInbound | None) -> dict[str, Any]:
        metadata: dict[str, Any] = {}
        if interaction is not None:
            metadata.update(
                {
                    "channel": interaction.channel,
                    "source": interaction.source,
                    "reply_to": interaction.reply_to,
                    "conversation_key": interaction.conversation_key,
                    **dict(interaction.metadata or {}),
                }
            )
        metadata.update(self.runtime_timezone.metadata())
        return {key: value for key, value in metadata.items() if value is not None}

    def _resolve_session_for_interaction(self, core: LoadedCore, interaction_metadata: dict[str, Any]) -> None:
        channel = interaction_metadata.get("channel")
        conversation_key = interaction_metadata.get("conversation_key")
        if not channel:
            return
        if not conversation_key:
            if self.sessions.can_bind_session(
                self.session_id,
                channel=str(channel),
                conversation_key=None,
            ):
                self.session_runtime.update_session(
                    self.session_id,
                    core_id=core.core_id,
                    core_revision=self._core_revision(core),
                    channel=str(channel),
                    conversation_key=None,
                    metadata={
                        key: value
                        for key, value in interaction_metadata.items()
                        if key not in {"channel", "conversation_key"}
                    },
                )
            return
        existing = self.sessions.resolve_interaction_session(
            core_id=core.core_id,
            channel=str(channel),
            conversation_key=str(conversation_key),
        )
        if existing:
            if existing != self.session_id:
                self._switch_session(existing, emit_resumed=True)
            return
        if self.sessions.can_bind_session(
            self.session_id,
            channel=str(channel),
            conversation_key=str(conversation_key),
        ):
            self.session_runtime.update_session(
                self.session_id,
                core_id=core.core_id,
                core_revision=self._core_revision(core),
                channel=str(channel),
                conversation_key=str(conversation_key),
                metadata={
                    key: value for key, value in interaction_metadata.items() if key not in {"channel", "conversation_key"}
                },
            )
            return
        self.start_new_session(
            channel=str(channel),
            conversation_key=str(conversation_key),
            source=interaction_metadata.get("source"),
            reply_to=interaction_metadata.get("reply_to"),
        )

    def _resolve_phase_slots(
        self,
        core: LoadedCore,
        kind: str,
        slot_ids: list[str] | tuple[str, ...] | None,
    ) -> list[SlotDefinition] | None:
        if slot_ids is None:
            return None
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

    def _resolve_child_agent_slots(
        self,
        core: LoadedCore,
        *,
        input_slots: ChildSlotRequest,
        output_slots: ChildSlotRequest,
        use_bootstrap: bool,
    ) -> ResolvedChildAgentSlots:
        if not isinstance(use_bootstrap, bool):
            raise ValueError("use_bootstrap must be a boolean")
        return ResolvedChildAgentSlots(
            input=self._resolve_child_phase_slots(core, "input", input_slots),
            output=self._resolve_child_phase_slots(core, "output", output_slots),
            use_bootstrap=use_bootstrap,
        )

    def _resolve_child_agent_slots_for_core_id(
        self,
        core_id: str,
        *,
        input_slots: ChildSlotRequest,
        output_slots: ChildSlotRequest,
        use_bootstrap: bool,
    ) -> ResolvedChildAgentSlots:
        core = self.core_loader.load(self.version_store.active_core_path(core_id))
        return self._resolve_child_agent_slots(
            core,
            input_slots=input_slots,
            output_slots=output_slots,
            use_bootstrap=use_bootstrap,
        )

    def _resolve_child_phase_slots(
        self,
        core: LoadedCore,
        kind: str,
        requested: ChildSlotRequest,
    ) -> ResolvedPhaseSlots:
        pipeline = core.input_pipeline if kind == "input" else core.output_pipeline
        default_ids = CHILD_AGENT_DEFAULT_INPUT_SLOTS if kind == "input" else CHILD_AGENT_DEFAULT_OUTPUT_SLOTS
        requested_ids = self._normalize_child_slot_request(kind, requested, default_ids=default_ids)
        if requested_ids == CHILD_AGENT_ALL_SLOTS:
            return ResolvedPhaseSlots(serial=list(pipeline.serial), parallel=list(pipeline.parallel))

        known_ids = {slot.slot_id for slot in (core.input_slots if kind == "input" else core.output_slots)}
        pipeline_ids = {slot.slot_id for slot in [*pipeline.serial, *pipeline.parallel]}
        for slot_id in requested_ids:
            if slot_id not in known_ids:
                raise ValueError(f"unknown {kind} slot id: {slot_id}")
            if slot_id not in pipeline_ids:
                raise ValueError(f"{kind} slot id is not in the active pipeline: {slot_id}")
        selected_ids = set(requested_ids)
        return ResolvedPhaseSlots(
            serial=[slot for slot in pipeline.serial if slot.slot_id in selected_ids],
            parallel=[slot for slot in pipeline.parallel if slot.slot_id in selected_ids],
        )

    def _normalize_child_slot_request(
        self,
        kind: str,
        requested: ChildSlotRequest,
        *,
        default_ids: tuple[str, ...],
    ) -> tuple[str, ...] | str:
        if requested is None:
            return default_ids
        if isinstance(requested, str):
            if requested == CHILD_AGENT_ALL_SLOTS:
                return CHILD_AGENT_ALL_SLOTS
            raise ValueError(f"{kind}_slots must be 'all' or a list of slot ids")
        if not isinstance(requested, Sequence):
            raise ValueError(f"{kind}_slots must be 'all' or a list of slot ids")
        if not requested:
            return default_ids
        normalized: list[str] = []
        seen: set[str] = set()
        for raw_id in requested:
            if not isinstance(raw_id, str):
                raise ValueError(f"{kind}_slots items must be strings")
            slot_id = raw_id.strip()
            if not slot_id:
                raise ValueError(f"{kind} slot id must not be empty")
            if slot_id in seen:
                raise ValueError(f"duplicate {kind} slot id: {slot_id}")
            seen.add(slot_id)
            normalized.append(slot_id)
        return tuple(normalized)

    def _child_slot_request_metadata(
        self,
        kind: str,
        requested: ChildSlotRequest,
        *,
        default_ids: tuple[str, ...],
    ) -> str | list[str]:
        normalized = self._normalize_child_slot_request(kind, requested, default_ids=default_ids)
        if normalized == CHILD_AGENT_ALL_SLOTS:
            return CHILD_AGENT_ALL_SLOTS
        return list(normalized)

    def _resolve_child_agent_tools(self, core: LoadedCore, requested: ChildToolRequest) -> ResolvedChildAgentTools:
        requested_tools = self._normalize_child_tool_request(requested)
        registry_entries = self.tool_runtime.registry_for(core)
        available_tool_ids = [entry.name for entry in registry_entries]
        available_tool_id_set = set(available_tool_ids)
        if requested_tools == CHILD_AGENT_ALL_TOOLS:
            return ResolvedChildAgentTools(
                requested=CHILD_AGENT_ALL_TOOLS,
                resolved=available_tool_ids,
            )
        if requested_tools == CHILD_AGENT_NO_TOOLS:
            return ResolvedChildAgentTools(
                requested=CHILD_AGENT_NO_TOOLS,
                resolved=[],
                tool_policy={"allow_exact": []},
            )

        for tool_id in requested_tools:
            if tool_id not in available_tool_id_set:
                raise ValueError(f"unknown child tool id: {tool_id}")
        selected = set(requested_tools)
        resolved = [tool_id for tool_id in available_tool_ids if tool_id in selected]
        return ResolvedChildAgentTools(
            requested=list(requested_tools),
            resolved=resolved,
            tool_policy={"allow_exact": resolved},
        )

    async def _resolve_child_agent_tools_for_core_id_prepared(
        self,
        core_id: str,
        requested: ChildToolRequest,
        *,
        session_id: str,
    ) -> ResolvedChildAgentTools:
        core = self.core_loader.load(self.version_store.active_core_path(core_id))
        return await self._resolve_child_agent_tools_prepared(core, requested, session_id=session_id)

    async def _resolve_child_agent_tools_prepared(
        self,
        core: LoadedCore,
        requested: ChildToolRequest,
        *,
        session_id: str,
    ) -> ResolvedChildAgentTools:
        normalized = self._normalize_child_tool_request(requested)
        if normalized != CHILD_AGENT_NO_TOOLS:
            await self._prepare_child_agent_tool_registry(core, session_id=session_id)
        return self._resolve_child_agent_tools(core, normalized)

    async def _prepare_child_agent_tool_registry(self, core: LoadedCore, *, session_id: str) -> None:
        if not core.mcp_servers:
            return
        turn = TurnContext(
            session_id=session_id,
            turn_id=utc_id("turn_child_tools_"),
            core_id=core.core_id,
            core_revision=self._core_revision(core),
            user_input=AgentInput(content=""),
            metadata={},
        )
        await self.tool_runtime.prepare_for_turn(core, turn, emit_event=self.event_log.emit)

    def _requested_child_agent_tools_for_core_id(self, core_id: str, requested: ChildToolRequest) -> str | list[str]:
        normalized = self._normalize_child_tool_request(requested)
        if isinstance(normalized, str):
            return normalized
        core = self.core_loader.load(self.version_store.active_core_path(core_id))
        available_tool_ids = {entry.name for entry in self.tool_runtime.registry_for(core)}
        missing = [tool_id for tool_id in normalized if tool_id not in available_tool_ids]
        if missing:
            unresolved_without_mcp_shape = [tool_id for tool_id in missing if "__" not in tool_id]
            if not core.mcp_servers or unresolved_without_mcp_shape:
                raise ValueError(f"unknown child tool id: {(unresolved_without_mcp_shape or missing)[0]}")
        return list(normalized)

    def _normalize_child_tool_request(self, requested: ChildToolRequest) -> tuple[str, ...] | str:
        if requested is None:
            return CHILD_AGENT_ALL_TOOLS
        if isinstance(requested, str):
            if requested in {CHILD_AGENT_ALL_TOOLS, CHILD_AGENT_NO_TOOLS}:
                return requested
            raise ValueError("tools must be 'all', 'none', or a list of tool ids")
        if not isinstance(requested, Sequence):
            raise ValueError("tools must be 'all', 'none', or a list of tool ids")
        if not requested:
            return CHILD_AGENT_NO_TOOLS
        normalized: list[str] = []
        seen: set[str] = set()
        for raw_id in requested:
            if not isinstance(raw_id, str):
                raise ValueError("tools items must be strings")
            tool_id = raw_id.strip()
            if not tool_id:
                raise ValueError("tool id must not be empty")
            if tool_id in seen:
                raise ValueError(f"duplicate tool id: {tool_id}")
            seen.add(tool_id)
            normalized.append(tool_id)
        return tuple(normalized)

    def _tool_result_model_content(self, result: ToolResult) -> str:
        return result.model_output if result.model_output is not None else result.content

    def turn_tool_result_model_content(self, result: ToolResult) -> str:
        return self._tool_result_model_content(result)

    def _truncate_model_content(self, content: str) -> str:
        if len(content) > 4000:
            return f"{content[:4000]}\n...[truncated {len(content) - 4000} chars]"
        return content

    def truncate_turn_model_content(self, content: str) -> str:
        return self._truncate_model_content(content)

    def _session_history_messages(self) -> list[LLMMessage]:
        messages: list[LLMMessage] = []
        for message in self.sessions.history_for_context(self.session_id):
            llm_message = self.context_assembler._session_message_to_llm(message)
            if llm_message is not None:
                messages.append(llm_message)
        return messages

    def _module_io_client(
        self,
        slot: SlotDefinition,
        *,
        turn: TurnContext,
        capability: CapabilityFacade,
        interaction_metadata: dict[str, Any],
        background: bool = False,
        items: list[InteractionItem] | None = None,
    ) -> ModuleIOClient:
        route = self._delivery_route_context(turn, slot, interaction_metadata)
        commit = lambda request: self._commit_module_delivery_request(
            request,
            turn=turn,
            slot=slot,
            interaction_metadata=interaction_metadata,
        )
        schedule = lambda item: self._schedule_interaction_item(
            item,
            turn=turn,
            interaction_metadata=interaction_metadata,
        )
        default_write_history = slot.history_policy != "transient"
        allow_write_history = True
        if slot.kind == "input":
            default_write_history = False
        elif slot.kind == "output":
            default_write_history = not background
            allow_write_history = not background
        return ModuleIOClient(
            home=self.home,
            session_id=self.session_id,
            workspace=self.workspace,
            default_history_policy=slot.history_policy,
            default_write_history=default_write_history,
            allow_write_history=allow_write_history,
            commit=commit,
            schedule=schedule,
            route=route,
            background=background,
            items=items,
        )

    def turn_output_client(
        self,
        slot: SlotDefinition,
        *,
        turn: TurnContext,
        capability: CapabilityFacade,
        interaction_metadata: dict[str, Any],
        items: list[InteractionItem],
    ) -> ModuleIOClient:
        return self._module_io_client(
            slot,
            turn=turn,
            capability=capability,
            interaction_metadata=interaction_metadata,
            items=items,
        )

    async def send_turn_assistant_step(
        self,
        *,
        turn: TurnContext,
        step_id: str,
        content: str,
        tool_calls: list[ToolCall],
        interaction_metadata: dict[str, Any],
    ) -> tuple[SessionMessage | None, list[InteractionItem]]:
        return await self.runtime_io.send_assistant_step(
            turn=turn,
            step_id=step_id,
            content=content,
            tool_calls=tool_calls,
            interaction_metadata=interaction_metadata,
        )

    async def send_turn_tool_call_started(
        self,
        *,
        turn: TurnContext,
        step_id: str,
        call: ToolCall,
        interaction_metadata: dict[str, Any],
    ) -> InteractionItem:
        return await self.runtime_io.send_tool_call_started(
            turn=turn,
            step_id=step_id,
            call=call,
            interaction_metadata=interaction_metadata,
        )

    async def send_turn_tool_call_finished(
        self,
        *,
        turn: TurnContext,
        step_id: str,
        record: ToolExecutionRecord,
        interaction_metadata: dict[str, Any],
    ) -> InteractionItem:
        return await self.runtime_io.send_tool_call_finished(
            turn=turn,
            step_id=step_id,
            record=record,
            interaction_metadata=interaction_metadata,
        )

    def _commit_module_delivery_request(
        self,
        request: DeliveryRequest,
        *,
        turn: TurnContext,
        slot: SlotDefinition,
        interaction_metadata: dict[str, Any],
    ) -> InteractionItem | None:
        item = self.runtime_io.send_module_output(
            request,
            turn=turn,
            slot=slot,
            interaction_metadata=interaction_metadata,
        )
        self.history = self._session_history_messages()
        return item

    def _schedule_interaction_item(
        self,
        item: InteractionItem,
        *,
        turn: TurnContext,
        interaction_metadata: dict[str, Any],
    ) -> None:
        if item.dispatch_status != "pending":
            return
        metadata = self._interaction_item_outbound_metadata(interaction_metadata, item)
        channel = metadata.get("channel") or interaction_metadata.get("channel")
        if not channel:
            item.set_dispatch_status("unrouted")
            return
        item.set_dispatch_status("scheduled")
        self._enqueue_interaction_item(
            item,
            turn=turn,
            metadata=metadata,
            channel=str(channel),
        )

    async def _dispatch_interaction_item_now(
        self,
        item: InteractionItem,
        *,
        turn: TurnContext,
        interaction_metadata: dict[str, Any],
    ) -> None:
        if item.dispatch_status != "pending":
            return
        metadata = self._interaction_item_outbound_metadata(interaction_metadata, item)
        channel = metadata.get("channel") or interaction_metadata.get("channel")
        if not channel:
            item.set_dispatch_status("unrouted")
            return
        item.set_dispatch_status("scheduled")
        await self.delivery_runtime.dispatch_item(
            item,
            session_id=self.session_id,
            turn_id=turn.turn_id,
            channel=str(channel),
            metadata=metadata,
            event_metadata=self._delivery_event_metadata(metadata),
        )

    def _schedule_slot_end_delivery_items(
        self,
        items: list[InteractionItem],
        *,
        turn: TurnContext,
        interaction_metadata: dict[str, Any],
    ) -> None:
        for item in items:
            self._schedule_interaction_item(
                item,
                turn=turn,
                interaction_metadata=interaction_metadata,
            )

    def _mark_slot_end_delivery_failed(self, items: list[InteractionItem], *, reason: str) -> None:
        for item in items:
            if item.delivery is None or item.dispatch_status != "pending":
                continue
            item.metadata["delivery_failed_reason"] = reason
            if item.delivery is not None:
                item.delivery.metadata = {
                    **dict(item.delivery.metadata),
                    "delivery_failed_reason": reason,
                }
            item.set_dispatch_status("failed")

    def _module_result_client(self, *, writable: bool) -> ModuleResultClient:
        return ModuleResultClient(
            home=self.home,
            session_id=self.session_id,
            workspace=self.workspace,
            writable=writable,
        )

    async def _run_child_agent(
        self,
        *,
        core_id: str,
        raw_input: str,
        parent_turn: TurnContext,
        parent_slot_path: str,
        context: list[str],
        input_slots: ChildSlotRequest = None,
        output_slots: ChildSlotRequest = None,
        use_bootstrap: bool = False,
        tools: ChildToolRequest = CHILD_AGENT_ALL_TOOLS,
        session_id: str | None = None,
    ) -> AgentRunResult:
        child_session_id = session_id or utc_id("session_child_")
        child_runner = SessionTurnStepRunner(
            home=self.home,
            version_store=self.version_store,
            core_loader=self.core_loader,
            provider=self.provider,
            tool_runtime=self.tool_runtime,
            core_id=core_id,
            session_id=child_session_id,
            model_override=self.model_override,
            model_resolver=self.model_resolver,
            provider_name=self.provider_name,
            workspace=self.workspace,
            show_system_prompt=self.show_system_prompt,
            runtime_timezone=self.runtime_timezone,
            task_worker=self.task_worker,
            session_runtime=self.session_runtime,
            slot_runtime=self.slot_runtime,
            interaction_router=self.interaction_router,
            prepare_live_core=self.prepare_live_core_callback,
        )
        await child_runner.prepare_live_core()
        child_core_path = child_runner.version_store.active_core_path(child_runner.core_id)
        child_core = child_runner.core_loader.load(child_core_path)
        child_slots = child_runner._resolve_child_agent_slots(
            child_core,
            input_slots=input_slots,
            output_slots=output_slots,
            use_bootstrap=use_bootstrap,
        )
        child_tools = await child_runner._resolve_child_agent_tools_prepared(
            child_core,
            tools,
            session_id=child_runner.session_id,
        )
        child_runner.session_runtime.update_session(
            child_runner.session_id,
            core_id=child_core.core_id,
            core_revision=child_runner._core_revision(child_core),
            provider=child_runner.provider_name,
            model=child_runner._resolve_model_name(child_core),
            touch=False,
        )
        child_slot_metadata = child_slots.to_metadata()
        child_tool_metadata = {"requested": child_tools.requested, "resolved": child_tools.resolved}
        child_metadata = {
            "delegation_depth": int(parent_turn.metadata.get("delegation_depth") or 0) + 1,
            "parent_session_id": parent_turn.session_id,
            "parent_turn_id": parent_turn.turn_id,
            "parent_slot": parent_slot_path,
            "child_agent_slots": child_slot_metadata,
            "child_agent_tools": child_tool_metadata,
        }
        if child_tools.tool_policy:
            child_metadata["tool_policy"] = child_tools.tool_policy
        result = await child_runner.run_turn(
            raw_input,
            core_path=child_core_path,
            interaction=InteractionInbound(
                channel="agent",
                text=raw_input,
                source=parent_turn.session_id,
                metadata=child_metadata,
            ),
            injected_system_context=context,
            input_phase_slots=child_slots.input,
            output_phase_slots=child_slots.output,
            use_bootstrap=child_slots.use_bootstrap,
        )
        await child_runner.drain_background_tasks(include_task_worker=False)
        needs_user = result.needs_user or self._turn_result_needs_user(result)
        return AgentRunResult(
            content="\n\n".join(delivery.text for delivery in result.deliveries if delivery.text).strip(),
            core_id=result.core_id,
            session_id=result.session_id,
            turn_id=result.turn_id,
            result=result.agent_result,
            deliveries=tuple(
                AgentDeliverySummary(
                    kind=delivery.kind,
                    text=delivery.text,
                    history_policy=delivery.history_policy,
                    visible=delivery.visible,
                )
                for delivery in result.deliveries
            ),
            tools=tuple(
                AgentToolSummary(
                    name=record.call.name,
                    content=record.result.content,
                    is_error=record.result.is_error,
                )
                for record in result.tool_results
            ),
            metadata={
                "parent_turn_id": parent_turn.turn_id,
                "parent_slot": parent_slot_path,
                "needs_user": needs_user,
                "child_agent_slots": child_slot_metadata,
                "child_agent_tools": child_tool_metadata,
            },
        )

    def _spawn_child_agent(
        self,
        *,
        core_id: str,
        raw_input: str,
        parent_turn: TurnContext,
        parent_slot_path: str,
        context: list[str],
        input_slots: ChildSlotRequest = None,
        output_slots: ChildSlotRequest = None,
        use_bootstrap: bool = False,
        tools: ChildToolRequest = CHILD_AGENT_ALL_TOOLS,
        notify_on_complete: bool = True,
        session_id: str | None = None,
        resolved_child_tools: ResolvedChildAgentTools | None = None,
    ) -> AgentSpawnHandle:
        self._resolve_child_agent_slots_for_core_id(
            core_id,
            input_slots=input_slots,
            output_slots=output_slots,
            use_bootstrap=use_bootstrap,
        )
        requested_tools = (
            resolved_child_tools.requested
            if resolved_child_tools is not None
            else self._requested_child_agent_tools_for_core_id(core_id, tools)
        )
        requested_slot_metadata = {
            "input_slots": self._child_slot_request_metadata(
                "input",
                input_slots,
                default_ids=CHILD_AGENT_DEFAULT_INPUT_SLOTS,
            ),
            "output_slots": self._child_slot_request_metadata(
                "output",
                output_slots,
                default_ids=CHILD_AGENT_DEFAULT_OUTPUT_SLOTS,
            ),
            "use_bootstrap": use_bootstrap,
        }
        session_id = session_id or utc_id("session_child_")

        async def run_task(ctx: RuntimeTaskContext) -> RuntimeTaskOutcome:
            self.event_log.emit(
                "agent_spawn.started",
                turn_id=parent_turn.turn_id,
                slot=parent_slot_path,
                task_id=ctx.task_id,
                child_core_id=core_id,
                child_session_id=session_id,
            )
            try:
                result = await self._run_child_agent(
                    core_id=core_id,
                    raw_input=raw_input,
                    parent_turn=parent_turn,
                    parent_slot_path=parent_slot_path,
                    context=context,
                    input_slots=input_slots,
                    output_slots=output_slots,
                    use_bootstrap=use_bootstrap,
                    tools=tools,
                    session_id=session_id,
                )
                child_slot_metadata = result.metadata.get("child_agent_slots")
                child_tool_metadata = result.metadata.get("child_agent_tools")
                summary = result.content or f"child agent {core_id} completed"
                ctx.update_metadata(
                    {
                        "child_core_id": core_id,
                        "child_session_id": session_id,
                        "child_turn_id": result.turn_id,
                        "needs_user": bool(result.metadata.get("needs_user")),
                        "resolved_child_agent_slots": child_slot_metadata,
                        "resolved_child_agent_tools": child_tool_metadata,
                    }
                )
                needs_user = bool(result.metadata.get("needs_user"))
                event_name = "agent_spawn.blocked" if needs_user else "agent_spawn.completed"
                if needs_user:
                    summary = summary or f"child agent {core_id} needs user input"
                    ctx.mark_blocked(summary, metadata={"needs_user": True})
                self.event_log.emit(
                    event_name,
                    turn_id=parent_turn.turn_id,
                    slot=parent_slot_path,
                    task_id=ctx.task_id,
                    child_core_id=core_id,
                    child_session_id=session_id,
                    child_turn_id=result.turn_id,
                )
                return RuntimeTaskOutcome(
                    summary=summary,
                    result_ref=f"session:{session_id}:{result.turn_id}",
                    metadata={
                        "child_core_id": core_id,
                        "child_session_id": session_id,
                        "child_turn_id": result.turn_id,
                        "needs_user": needs_user,
                        "resolved_child_agent_slots": child_slot_metadata,
                        "resolved_child_agent_tools": child_tool_metadata,
                    },
                )
            except Exception as exc:
                self.event_log.emit(
                    "agent_spawn.failed",
                    turn_id=parent_turn.turn_id,
                    slot=parent_slot_path,
                    task_id=ctx.task_id,
                    child_core_id=core_id,
                    child_session_id=session_id,
                    error=str(exc),
                )
                raise

        try:
            record = self.task_worker.start_task(
                kind="agent.spawn",
                owner_session_id=parent_turn.session_id,
                owner_turn_id=parent_turn.turn_id,
                source_tool="agents.spawn",
                task_factory=run_task,
                write_scope=f"agent-session:{session_id}",
                notify_on_complete=notify_on_complete,
                metadata={
                    "child_core_id": core_id,
                    "child_session_id": session_id,
                    "parent_slot": parent_slot_path,
                    "requested_child_agent_slots": requested_slot_metadata,
                    "requested_child_agent_tools": requested_tools,
                },
            )
        except RuntimeTaskConflictError as exc:
            self.event_log.emit(
                "agent_spawn.rejected",
                turn_id=parent_turn.turn_id,
                slot=parent_slot_path,
                child_core_id=core_id,
                child_session_id=session_id,
                error=str(exc),
            )
            return AgentSpawnHandle(task_id="", core_id=core_id, session_id=session_id, status="failed")
        return AgentSpawnHandle(task_id=record.task_id, core_id=core_id, session_id=session_id)

    def _turn_result_needs_user(self, result: TurnResult) -> bool:
        for record in result.tool_results:
            data = record.result.data
            if not isinstance(data, Mapping):
                continue
            if data.get("needs_user"):
                return True
            approval = data.get("approval")
            if isinstance(approval, Mapping):
                reason = str(approval.get("reason") or "").lower()
                if approval.get("value") == "deny" and "no_interactive_route" in reason:
                    return True
        return False

    async def execute_tool(
        self,
        call: ToolCall,
        *,
        core: LoadedCore,
        turn: TurnContext,
        capability: CapabilityFacade,
        emit_event: Callable[..., dict[str, Any]] | None = None,
        output_factory: Callable[[SlotDefinition], Any] | None = None,
    ) -> ToolResult:
        if call.name in DELEGATION_TOOL_NAMES:
            return await self.handle_delegation_tool(call, core=core, turn=turn, capability=capability)
        return await self.tool_runtime.execute(
            call,
            core=core,
            turn=turn,
            capability=capability,
            emit_event=emit_event,
            output_factory=output_factory,
        )

    async def execute_turn_tool(
        self,
        call: ToolCall,
        *,
        core: LoadedCore,
        turn: TurnContext,
        capability: CapabilityFacade,
        output_factory: Callable[[SlotDefinition], Any],
    ) -> ToolResult:
        return await self.execute_tool(
            call,
            core=core,
            turn=turn,
            capability=capability,
            emit_event=self.event_log.emit,
            output_factory=output_factory,
        )

    async def handle_delegation_tool(
        self,
        call: ToolCall,
        *,
        core: LoadedCore,
        turn: TurnContext,
        capability: CapabilityFacade,
    ) -> ToolResult:
        try:
            visible_tools = {entry.name for entry in self.tool_runtime.registry_for(core, turn=turn)}
            if call.name not in visible_tools:
                return ToolResult(content=f"builtin tool is not allowed: {call.name}", is_error=True)
            if call.name == "delegate_task":
                return await self._delegate_task(call, core=core, turn=turn, capability=capability)
            if call.name == "task_status":
                capability.require("task.control")
                return self._task_status(call)
            if call.name == "task_control":
                capability.require("task.control")
                return await self._task_control(call)
            if call.name == "yield_until":
                capability.require("task.control")
                return await self._yield_until(call)
            return ToolResult(content=f"unsupported delegation tool: {call.name}", is_error=True)
        except CapabilityDenied as exc:
            return ToolResult(content=str(exc), is_error=True, data={"executionStarted": False})

    async def _delegate_task(
        self,
        call: ToolCall,
        *,
        core: LoadedCore,
        turn: TurnContext,
        capability: CapabilityFacade,
    ) -> ToolResult:
        goal = str(call.arguments.get("goal") or "").strip()
        if not goal:
            return ToolResult(content="goal is required", is_error=True)
        if "tool_policy" in call.arguments:
            return ToolResult(content="delegate_task tool_policy is not supported; use tools", is_error=True)
        child_core_id = str(call.arguments.get("core_id") or core.core_id).strip()
        capability.require(f"agents.spawn:{child_core_id}")
        context_mode = str(call.arguments.get("context_mode") or "isolated").strip()
        if context_mode not in {"isolated", "fork"}:
            return ToolResult(content=f"unsupported context_mode: {context_mode}", is_error=True)
        depth = int(turn.metadata.get("delegation_depth") or 0)
        max_depth = int(call.arguments.get("max_depth") or 2)
        if depth >= max_depth:
            return ToolResult(content=f"delegation depth limit exceeded: max_depth={max_depth}", is_error=True)
        total_children = [
            task
            for task in self.task_worker.list_tasks(owner_session_id=turn.session_id, kind="agent.spawn")
            if task.owner_turn_id == turn.turn_id
        ]
        if len(total_children) >= 4:
            return ToolResult(content="delegation limit exceeded: max_children=4", is_error=True)
        running_children = self.task_worker.list_tasks(
            owner_session_id=turn.session_id,
            kind="agent.spawn",
            include_completed=False,
        )
        if len(running_children) >= 2:
            return ToolResult(content="delegation limit exceeded: max_concurrent_children=2", is_error=True)
        notify_policy = str(call.arguments.get("notify_policy") or "return_to_parent").strip()
        if notify_policy not in {"return_to_parent", "silent"}:
            return ToolResult(content=f"unsupported notify_policy: {notify_policy}", is_error=True)
        raw_use_bootstrap = call.arguments.get("use_bootstrap", False)
        use_bootstrap = False if raw_use_bootstrap is None else raw_use_bootstrap
        context = self._delegation_context(context_mode)
        child_tools_request = call.arguments.get("tools", CHILD_AGENT_ALL_TOOLS)
        child_session_id = utc_id("session_child_")
        try:
            child_tools = await self._resolve_child_agent_tools_for_core_id_prepared(
                child_core_id,
                child_tools_request,
                session_id=child_session_id,
            )
            handle = self._spawn_child_agent(
                core_id=child_core_id,
                raw_input=goal,
                parent_turn=turn,
                parent_slot_path="builtin:delegate_task",
                context=context,
                input_slots=call.arguments.get("input_slots"),
                output_slots=call.arguments.get("output_slots"),
                use_bootstrap=use_bootstrap,
                tools=child_tools_request,
                notify_on_complete=notify_policy == "return_to_parent",
                session_id=child_session_id,
                resolved_child_tools=child_tools,
            )
        except ValueError as exc:
            return ToolResult(content=str(exc), is_error=True)
        payload = {"task_id": handle.task_id}
        return ToolResult(content=json.dumps(payload, ensure_ascii=False), data=payload)

    def _delegation_context(self, context_mode: str) -> list[str]:
        if context_mode == "isolated":
            return []
        messages = self.sessions.history_for_context(self.session_id)[-12:]
        if not messages:
            return []
        transcript = "\n".join(f"{message.role}: {message.content}" for message in messages if message.content.strip())
        return [f"Parent session fork context:\n{transcript}"] if transcript.strip() else []

    def _task_status(self, call: ToolCall) -> ToolResult:
        task_id = str(call.arguments.get("task_id") or "").strip()
        if not task_id:
            return ToolResult(content="task_id is required", is_error=True)
        view = str(call.arguments.get("view") or "model").strip()
        payload = self._task_view(task_id, include_log=view in {"operator", "debug"})
        if payload is None:
            return ToolResult(content=f"background task not found: {task_id}", is_error=True)
        content = json.dumps(payload, ensure_ascii=False)
        return ToolResult(content=content, data=payload, model_output=content)

    async def _task_control(self, call: ToolCall) -> ToolResult:
        task_id = str(call.arguments.get("task_id") or "").strip()
        if not task_id:
            return ToolResult(content="task_id is required", is_error=True)
        command = str(call.arguments.get("command") or "cancel").strip()
        if command != "cancel":
            return ToolResult(content=f"unsupported task_control command: {command}", is_error=True)
        try:
            record = await self.task_worker.cancel(task_id)
            payload = record.to_payload(include_log=True, log=self.task_worker.log(task_id))
        except (KeyError, RuntimeTaskKindError):
            return ToolResult(content=f"background task not found: {task_id}", is_error=True)
        return ToolResult(content=json.dumps(payload, ensure_ascii=False), data=payload)

    async def _yield_until(self, call: ToolCall) -> ToolResult:
        task_id = str(call.arguments.get("task_id") or "").strip()
        if not task_id:
            return ToolResult(content="task_id is required", is_error=True)
        raw_timeout = call.arguments.get("timeout_seconds")
        timeout = float(raw_timeout if raw_timeout is not None else 30)
        try:
            record = await self.task_worker.wait(task_id, timeout_seconds=timeout, consume_completion=True)
        except (KeyError, RuntimeTaskKindError):
            return ToolResult(content=f"background task not found: {task_id}", is_error=True)
        except asyncio.TimeoutError:
            payload = self._task_view(task_id, include_log=False) or {"task_id": task_id, "status": "unknown"}
            payload["timed_out"] = True
            return ToolResult(content=json.dumps(payload, ensure_ascii=False), data=payload)
        payload = record.to_payload(include_log=True, log=self.task_worker.log(task_id))
        return ToolResult(content=json.dumps(payload, ensure_ascii=False), data=payload)

    def _task_view(self, task_id: str, *, include_log: bool) -> dict[str, Any] | None:
        try:
            record = self.task_worker.get(task_id)
        except (KeyError, RuntimeTaskKindError):
            return None
        return record.to_payload(include_log=include_log, log=self.task_worker.log(task_id) if include_log else None)

    async def _run_input_slots(
        self,
        core: LoadedCore,
        turn: TurnContext,
        capability: CapabilityFacade,
        envelope: InputEnvelope,
        state_stores: ModuleStateStores,
        *,
        interaction_metadata: dict[str, Any],
        injected_system_context: list[str],
        serial_slots: list[SlotDefinition] | None = None,
        phase_slots: ResolvedPhaseSlots | None = None,
    ) -> tuple[str, str, list[ContextContribution], list[InteractionItem]]:
        if phase_slots is not None:
            parallel_slots = phase_slots.parallel
            current_serial_slots = phase_slots.serial
        else:
            parallel_slots = [] if serial_slots is not None else core.input_pipeline.parallel
            current_serial_slots = serial_slots or core.input_pipeline.serial
        result = await self.slot_pipeline.run_input(
            InputPipelineRequest(
                core=core,
                turn=turn,
                capability=capability,
                envelope=envelope,
                state_stores=state_stores,
                interaction_metadata=interaction_metadata,
                injected_system_context=injected_system_context,
                serial_slots=current_serial_slots,
                parallel_slots=parallel_slots,
            )
        )
        return result.user_text, result.persisted_user_text, result.context, result.items

    async def run_input_pipeline_slot(self, request: InputSlotRunRequest) -> list[InteractionItem]:
        return await self._run_input_slot(
            request.slot,
            core=request.core,
            turn=request.turn,
            capability=request.capability,
            envelope=request.envelope,
            raw_input=request.raw_input,
            builder=request.builder,
            builder_writable=request.builder_writable,
            state_stores=request.state_stores,
            interaction_metadata=request.interaction_metadata,
            activated=request.activated,
            contributions=request.contributions,
            background=request.background,
        )

    async def _run_input_slot(
        self,
        slot: SlotDefinition,
        *,
        core: LoadedCore,
        turn: TurnContext,
        capability: CapabilityFacade,
        envelope: InputEnvelope,
        raw_input: RawInput,
        builder: ModuleInputBuilder,
        builder_writable: bool,
        state_stores: ModuleStateStores,
        interaction_metadata: dict[str, Any],
        activated: set[str],
        contributions: list[ContextContribution],
        background: bool = False,
    ) -> list[InteractionItem]:
        items: list[InteractionItem] = []
        io_client = self._module_io_client(
            slot,
            turn=turn,
            capability=capability,
            interaction_metadata=interaction_metadata,
            background=background,
            items=items,
        )
        ctx = InputContext(
            turn=turn,
            slot_id=slot.slot_id,
            slot_path=slot.relative_path,
            capability=capability,
            input=ModuleInputClient(raw_input=raw_input, builder=builder, writable=builder_writable, sender=io_client),
            history=ModuleHistoryClient(sessions=self.sessions, session_id=self.session_id),
            agents=ModuleAgentsClient(
                parent=self,
                capability=capability,
                slot_path=slot.relative_path,
                turn=turn,
                interaction_metadata=interaction_metadata,
                emit_event=self.event_log.emit,
            ),
            state=ModuleStateClient(
                state_stores=state_stores,
                capability=capability,
                slot_path=slot.relative_path,
                turn_id=turn.turn_id,
                emit_event=self.event_log.emit,
            ),
            tools=ModuleToolClient(
                parent=self,
                tool_runtime=self.tool_runtime,
                core=core,
                turn=turn,
                capability=capability,
                slot_path=slot.relative_path,
                interaction_metadata=interaction_metadata,
                emit_event=self.event_log.emit,
                items=items,
            ),
            skills=ModuleSkillClient(
                envelope=envelope,
                capability=capability,
                slot_path=slot.relative_path,
                turn_id=turn.turn_id,
                emit_event=self.event_log.emit,
            ),
        )
        try:
            prior_envelope_activations = set(envelope.activated_skills)
            value = await self._call_slot(slot, ctx)
            if value is not None:
                self.event_log.emit("module.return_ignored", turn_id=turn.turn_id, slot=slot.relative_path, kind="input")
            for name in [name for name in envelope.activated_skills if name not in prior_envelope_activations]:
                if name in activated:
                    continue
                self._require_skill_activation(capability, slot.relative_path, name)
                skill = core.skill_by_id(name)
                if skill is None:
                    activated.add(name)
                    self.event_log.emit("skill.activation_ignored", turn_id=turn.turn_id, slot=slot.relative_path, skill=name)
                    continue
                activated.add(name)
                contributions.append(
                    ContextContribution(type="skill", key=skill.name, content=skill.content, placement="system_context")
                )
                self.event_log.emit("skill.activated", turn_id=turn.turn_id, slot=slot.relative_path, skill=skill.name)
            self._schedule_slot_end_delivery_items(
                io_client.slot_end_items,
                turn=turn,
                interaction_metadata=interaction_metadata,
            )
            self.event_log.emit("module.completed", turn_id=turn.turn_id, slot=slot.relative_path, kind="input")
            return io_client.items
        except Exception as exc:
            self._mark_slot_end_delivery_failed(io_client.slot_end_items, reason="slot_failed")
            self.event_log.emit(
                "module.failed",
                turn_id=turn.turn_id,
                slot=slot.relative_path,
                kind="input",
                error=str(exc),
            )
            if slot.failure_policy == "hard":
                raise
            return io_client.items

    def _require_skill_activation(self, capability: CapabilityFacade, slot_path: str, name: str) -> None:
        scoped = f"skill.activate:{name}"
        if capability.can(scoped, slot_path=slot_path):
            capability.require(scoped, slot_path=slot_path)
            return
        capability.require("skill.activate", slot_path=slot_path)

    async def _run_output_slots(
        self,
        core: LoadedCore,
        turn: TurnContext,
        capability: CapabilityFacade,
        *,
        current_output: str,
        tool_records: list[ToolExecutionRecord],
        state_stores: ModuleStateStores,
        interaction_metadata: dict[str, Any],
        result_client: ModuleResultClient,
        serial_slots: list[SlotDefinition] | None = None,
        phase_slots: ResolvedPhaseSlots | None = None,
    ) -> list[InteractionItem]:
        if phase_slots is not None:
            parallel_slots = phase_slots.parallel
            current_serial_slots = phase_slots.serial
        else:
            parallel_slots = [] if serial_slots is not None else core.output_pipeline.parallel
            current_serial_slots = serial_slots or core.output_pipeline.serial
        return await self.slot_pipeline.run_output(
            OutputPipelineRequest(
                core=core,
                turn=turn,
                capability=capability,
                current_output=current_output,
                tool_records=tool_records,
                state_stores=state_stores,
                interaction_metadata=interaction_metadata,
                result_client=result_client,
                serial_slots=current_serial_slots,
                parallel_slots=parallel_slots,
            )
        )

    async def run_output_pipeline_slot(self, request: OutputSlotRunRequest) -> list[InteractionItem]:
        return await self._run_output_slot(
            request.slot,
            core=request.core,
            turn=request.turn,
            capability=request.capability,
            envelope=request.envelope,
            current_output=request.current_output,
            tool_records=request.tool_records,
            state_stores=request.state_stores,
            interaction_metadata=request.interaction_metadata,
            result_client=request.result_client,
            background=request.background,
        )

    async def _run_output_slot(
        self,
        slot: SlotDefinition,
        *,
        core: LoadedCore,
        turn: TurnContext,
        capability: CapabilityFacade,
        envelope: OutputEnvelope,
        current_output: str,
        tool_records: list[ToolExecutionRecord],
        state_stores: ModuleStateStores,
        interaction_metadata: dict[str, Any],
        result_client: ModuleResultClient,
        background: bool = False,
    ) -> list[InteractionItem]:
        items: list[InteractionItem] = []
        io_client = self._module_io_client(
            slot,
            turn=turn,
            capability=capability,
            interaction_metadata=interaction_metadata,
            background=background,
            items=items,
        )
        ctx = OutputContext(
            turn=turn,
            slot_id=slot.slot_id,
            slot_path=slot.relative_path,
            capability=capability,
            output=ModuleOutputClient(content=current_output, metadata=envelope.metadata, sender=io_client),
            history=ModuleHistoryClient(sessions=self.sessions, session_id=self.session_id),
            agents=ModuleAgentsClient(
                parent=self,
                capability=capability,
                slot_path=slot.relative_path,
                turn=turn,
                interaction_metadata=interaction_metadata,
                emit_event=self.event_log.emit,
            ),
            state=ModuleStateClient(
                state_stores=state_stores,
                capability=capability,
                slot_path=slot.relative_path,
                turn_id=turn.turn_id,
                emit_event=self.event_log.emit,
            ),
            tools=ModuleToolClient(
                parent=self,
                tool_runtime=self.tool_runtime,
                core=core,
                turn=turn,
                capability=capability,
                slot_path=slot.relative_path,
                interaction_metadata=interaction_metadata,
                emit_event=self.event_log.emit,
                items=items,
            ),
            result=result_client,
        )
        try:
            value = await self._call_slot(slot, ctx)
            if value is not None:
                self.event_log.emit("module.return_ignored", turn_id=turn.turn_id, slot=slot.relative_path, kind="output")
            self._schedule_slot_end_delivery_items(
                io_client.slot_end_items,
                turn=turn,
                interaction_metadata=interaction_metadata,
            )
            self.history = self._session_history_messages()
            self.event_log.emit(
                "module.completed",
                turn_id=turn.turn_id,
                slot=slot.relative_path,
                kind="output",
            )
            return io_client.items
        except Exception as exc:
            self._mark_slot_end_delivery_failed(io_client.slot_end_items, reason="slot_failed")
            self.event_log.emit(
                "module.failed",
                turn_id=turn.turn_id,
                slot=slot.relative_path,
                kind="output",
                error=str(exc),
            )
            if slot.failure_policy == "hard":
                raise
            return io_client.items

    async def flush_slot_background_items(
        self,
        items: list[InteractionItem],
        *,
        turn: TurnContext,
        interaction_metadata: dict[str, Any],
    ) -> None:
        await self._flush_pending_background_items(
            items,
            turn=turn,
            interaction_metadata=interaction_metadata,
        )

    def _delivery_route_context(
        self,
        turn: TurnContext,
        slot: SlotDefinition,
        interaction_metadata: dict[str, Any],
    ) -> DeliveryRouteContext:
        return DeliveryRouteContext(
            session_id=self.session_id,
            turn_id=turn.turn_id,
            channel=interaction_metadata.get("channel"),
            conversation_key=interaction_metadata.get("conversation_key"),
            source=interaction_metadata.get("source"),
            reply_to=interaction_metadata.get("reply_to"),
            slot=slot.relative_path,
            metadata=dict(interaction_metadata),
        )

    def _enqueue_interaction_item(
        self,
        item: InteractionItem,
        *,
        turn: TurnContext,
        metadata: dict[str, Any],
        channel: str,
    ) -> None:
        task = asyncio.create_task(
            self.delivery_runtime.dispatch_item(
                item,
                session_id=self.session_id,
                turn_id=turn.turn_id,
                channel=channel,
                metadata=metadata,
                event_metadata=self._delivery_event_metadata(metadata),
            )
        )
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _flush_pending_background_items(
        self,
        items: list[InteractionItem],
        *,
        turn: TurnContext,
        interaction_metadata: dict[str, Any],
    ) -> None:
        for item in items:
            if item.dispatch_status != "pending":
                continue
            metadata = self._interaction_item_outbound_metadata(interaction_metadata, item)
            channel = metadata.get("channel") or interaction_metadata.get("channel")
            if not channel:
                item.set_dispatch_status("unrouted")
                continue
            item.set_dispatch_status("scheduled")
            await self.delivery_runtime.dispatch_item(
                item,
                session_id=self.session_id,
                turn_id=turn.turn_id,
                channel=str(channel),
                metadata=metadata,
                event_metadata=self._delivery_event_metadata(metadata),
            )

    async def _handle_effects(
        self,
        effects: list[EffectRequest | dict[str, Any]],
        *,
        core: LoadedCore,
        turn: TurnContext,
        capability: CapabilityFacade,
        slot: SlotDefinition,
        interaction_metadata: dict[str, Any],
    ) -> list[InteractionDelivery]:
        deliveries: list[InteractionDelivery] = []
        for raw_effect in effects:
            effect = self._normalize_effect(raw_effect)
            try:
                if effect.type == "append_assistant_message" and effect.content:
                    effect = EffectRequest(
                        type="deliver",
                        payload={"type": "text", "text": effect.content},
                        visible=effect.visible,
                        history_policy=effect.history_policy,
                    )
                if effect.type == "deliver":
                    delivery = self._apply_deliver_effect(
                        effect,
                        turn=turn,
                        slot=slot,
                        interaction_metadata=interaction_metadata,
                    )
                    if delivery:
                        deliveries.append(delivery)
                elif effect.type == "append_assistant_message" and effect.content:
                    if effect.visible:
                        deliveries.append(
                            InteractionDelivery(
                                type="text",
                                text=effect.content,
                                payload={"type": "text", "text": effect.content},
                                visible=True,
                                history_policy=effect.history_policy or slot.history_policy,
                                metadata={"slot": slot.relative_path},
                            )
                        )
                elif effect.type == "evolve_core":
                    capability.require("tool.call:evolve_core", slot_path=slot.relative_path)
                    result = await self.tool_runtime._execute_builtin(
                        ToolCall(name="evolve_core", arguments={"goal": effect.goal or effect.reason or ""}),
                        core=core,
                        turn=turn,
                        capability=capability,
                    )
                    deliveries.append(
                        InteractionDelivery(
                            type="text",
                            text=result.content,
                            payload={"type": "text", "text": result.content},
                            metadata={"slot": slot.relative_path, "effect": effect.type},
                        )
                    )
                elif effect.type == "tool_call" and effect.tool_name:
                    capability.require(f"tool.call:{effect.tool_name}", slot_path=slot.relative_path)
                    result = await self.execute_tool(
                        ToolCall(name=effect.tool_name, arguments=dict(effect.arguments or {})),
                        core=core,
                        turn=turn,
                        capability=capability,
                        emit_event=self.event_log.emit,
                    )
                    if result.content:
                        deliveries.append(
                            InteractionDelivery(
                                type="text",
                                text=result.content,
                                payload={"type": "text", "text": result.content},
                                metadata={"slot": slot.relative_path, "effect": effect.type},
                            )
                        )
                else:
                    self.event_log.emit(
                        "effect.ignored",
                        turn_id=turn.turn_id,
                        slot=slot.relative_path,
                        effect_type=effect.type,
                    )
            except CapabilityDenied as exc:
                self.event_log.emit(
                    "capability.denied",
                    turn_id=turn.turn_id,
                    slot=slot.relative_path,
                    error=str(exc),
                )
        return deliveries

    def _apply_delivery_request(
        self,
        request: DeliveryRequest,
        *,
        turn: TurnContext,
        slot: SlotDefinition,
        interaction_metadata: dict[str, Any],
    ) -> InteractionDelivery | None:
        history_policy = request.history_policy or slot.history_policy
        if history_policy not in {"persist", "model_hidden", "transient"}:
            raise ValueError(f"invalid history_policy: {history_policy}")
        if request.delivery not in DELIVERY_MODES:
            raise ValueError(f"invalid delivery mode: {request.delivery}")
        if request.kind not in {"message", "progress", "notice"}:
            raise ValueError(f"invalid delivery kind: {request.kind}")
        if request.target != "current":
            raise ValueError(f"unsupported delivery target: {request.target}")

        artifact_store = ArtifactStore(self.home, self.session_id)
        history_blocks: list[dict[str, Any]] = []
        delivery_blocks: list[dict[str, Any]] = []
        artifacts: list[ArtifactRef] = []
        delivery_artifacts: list[dict[str, Any]] = []
        fallback_lines: list[str] = []
        unsupported_blocks = 0

        for raw_block in request.blocks:
            block = raw_block if isinstance(raw_block, ContentBlock) else ContentBlock(**dict(raw_block))
            if block.type not in CONTENT_BLOCK_TYPES:
                unsupported_blocks += 1
                continue
            if block.type == "text":
                text = str(block.text or "")
                if text:
                    fallback_lines.append(text)
                block_dict = {"type": "text", "text": text, "metadata": dict(block.metadata)}
                history_blocks.append(block_dict)
                delivery_blocks.append(block_dict)
                continue
            if block.type == "control":
                unsupported_blocks += 1
                history_blocks.append({"type": "control", "text": block.text, "metadata": dict(block.metadata)})
                delivery_blocks.append({"type": "control", "text": block.text, "metadata": dict(block.metadata)})
                continue
            if block.artifact is None:
                unsupported_blocks += 1
                continue
            artifact = artifact_store.store(artifact_input_to_dict(block.artifact))
            artifacts.append(artifact)
            history_artifact = asdict(artifact)
            delivery_artifact = self._delivery_artifact_dict(artifact)
            delivery_artifacts.append(delivery_artifact)
            if block.text:
                fallback_lines.append(str(block.text))
            history_blocks.append(
                {
                    "type": block.type,
                    "text": block.text,
                    "artifact": history_artifact,
                    "metadata": dict(block.metadata),
                }
            )
            delivery_blocks.append(
                {
                    "type": block.type,
                    "text": block.text,
                    "artifact": delivery_artifact,
                    "metadata": dict(block.metadata),
                }
            )
            self._append_runtime_event(
                RuntimeEvent(
                    type="artifact.stored",
                    aggregate_type="artifact",
                    aggregate_id=artifact.artifact_id,
                    payload={
                        "task_id": turn.turn_id,
                        "kind": artifact.kind,
                        "uri": artifact.path or artifact.url or "",
                        "metadata": {
                            "session_id": self.session_id,
                            "turn_id": turn.turn_id,
                            "media_type": artifact.media_type,
                            "summary": artifact.summary,
                            **dict(artifact.metadata),
                        },
                    },
                )
            )

        fallback_text = "\n\n".join(line for line in fallback_lines if line).strip()
        writes_history = history_policy != "transient"
        has_non_text_history = any(block.get("type") != "text" for block in history_blocks)
        history_text = request.history_text
        if history_text is None and not has_non_text_history:
            history_text = fallback_text
        if writes_history and has_non_text_history and not (history_text or "").strip():
            raise ValueError("non-text send_* with write_history=True requires history_text")
        failure_history_text = request.failure_history_text if request.failure_history_text is not None else history_text
        metadata = {
            "slot": slot.relative_path,
            "phase": slot.kind,
            "delivery_id": request.delivery_id,
            "kind": request.kind,
            "blocks": history_blocks,
            "history_policy": history_policy,
            "delivery": request.delivery,
            "delivery_status": "pending",
            "artifacts": [asdict(artifact) for artifact in artifacts],
            "history_text": history_text,
            "failure_history_text": failure_history_text,
            **dict(request.metadata),
        }
        content = history_text or ""
        message_id = None
        delivery_payload = {
            "kind": request.kind,
            "visible": request.visible,
            "history_policy": history_policy,
            "message_id": None,
            "history_text": history_text,
            "failure_history_text": failure_history_text,
            "fallback_text": fallback_text,
            "blocks": delivery_blocks,
            "artifacts": delivery_artifacts,
        }
        delivery_target = {
            "conversation_key": interaction_metadata.get("conversation_key"),
            "source": interaction_metadata.get("source"),
            "reply_to": interaction_metadata.get("reply_to"),
        }
        if writes_history:
            message = self.session_runtime.append_delivery_message(
                self.session_id,
                role="assistant",
                content=content,
                delivery_id=request.delivery_id,
                task_id=turn.turn_id,
                channel=interaction_metadata.get("channel"),
                target=delivery_target,
                delivery_payload=delivery_payload,
                delivery_idempotency_key=request.delivery_id,
                turn_id=turn.turn_id,
                visible=request.visible,
                model_visible=history_policy == "persist",
                interaction_metadata=interaction_metadata,
                metadata=metadata,
            )
            message_id = message.id
            metadata["message_id"] = message_id
            delivery_payload["message_id"] = message_id
            self.event_log.emit(
                "message.persisted",
                turn_id=turn.turn_id,
                message_id=message.id,
                role=message.role,
                kind=message.kind,
                **interaction_metadata,
            )
        else:
            self._append_runtime_events(
                [
                    RuntimeEvent(
                        type="delivery.queued",
                        aggregate_type="delivery",
                        aggregate_id=request.delivery_id,
                        payload={
                            "task_id": turn.turn_id,
                            "channel": interaction_metadata.get("channel"),
                            "target": delivery_target,
                            "status": "queued",
                            "idempotency_key": request.delivery_id,
                            "payload": delivery_payload,
                        },
                    ),
                    durable_work_enqueued_event(
                        DurableWorkSpec(
                            work_id=request.delivery_id,
                            kind="delivery.send",
                            owner_session_id=self.session_id,
                            owner_turn_id=turn.turn_id,
                            parent_work_id=turn.turn_id,
                            payload={
                                "task_id": turn.turn_id,
                                "channel": interaction_metadata.get("channel"),
                                "target": delivery_target,
                                "idempotency_key": request.delivery_id,
                                **delivery_payload,
                            },
                        )
                    ),
                ]
            )
        self.event_log.emit(
            "delivery.completed",
            turn_id=turn.turn_id,
            slot=slot.relative_path,
            delivery_id=request.delivery_id,
            kind=request.kind,
            message_id=message_id,
            visible=request.visible,
            history_policy=history_policy,
            artifacts=[artifact.artifact_id for artifact in artifacts],
            **interaction_metadata,
        )
        if unsupported_blocks:
            self.event_log.emit(
                "delivery.degraded",
                turn_id=turn.turn_id,
                slot=slot.relative_path,
                delivery_id=request.delivery_id,
                reason="unsupported_blocks",
                count=unsupported_blocks,
                **interaction_metadata,
            )
        if interaction_metadata.get("channel") == "tui" and any(block.get("type") not in {"text"} for block in delivery_blocks):
            self.event_log.emit(
                "delivery.degraded",
                turn_id=turn.turn_id,
                slot=slot.relative_path,
                delivery_id=request.delivery_id,
                reason="channel_text_fallback",
                channel="tui",
            )
        if request.visible and (fallback_text or delivery_artifacts or delivery_blocks):
            first_type = next((block.get("type") for block in delivery_blocks if block.get("type") != "text"), "text")
            return InteractionDelivery(
                type=str(first_type or "text"),
                kind=request.kind,
                text=fallback_text,
                fallback_text=fallback_text,
                blocks=delivery_blocks,
                payload={"type": "blocks", "blocks": delivery_blocks},
                artifacts=delivery_artifacts,
                visible=request.visible,
                history_policy=history_policy,
                metadata=metadata,
            )
        return None

    def _delivery_artifact_dict(self, artifact: ArtifactRef) -> dict[str, Any]:
        data = asdict(artifact)
        path = artifact.path
        if path:
            raw_path = Path(path)
            if raw_path.is_absolute():
                data["resolved_path"] = str(raw_path)
            else:
                data["resolved_path"] = str((self.home / "runtime" / "artifacts" / self.session_id / path).resolve())
        return data

    def _apply_deliver_effect(
        self,
        effect: EffectRequest,
        *,
        turn: TurnContext,
        slot: SlotDefinition,
        interaction_metadata: dict[str, Any],
    ) -> InteractionDelivery | None:
        payload = effect.payload if effect.payload is not None else {"type": "text", "text": effect.content or ""}
        text = self._payload_text(payload)
        blocks: list[ContentBlock] = []
        if text:
            blocks.append(ContentBlock(type="text", text=text))
        for attachment in effect.attachments:
            kind = "file"
            if isinstance(attachment, Mapping):
                kind = str(attachment.get("kind") or kind)
            blocks.append(ContentBlock(type=kind if kind in CONTENT_BLOCK_TYPES else "file", artifact=attachment))
        request = DeliveryRequest(
            delivery_id=utc_id("delivery_"),
            blocks=blocks,
            history_policy=effect.history_policy or slot.history_policy,
            visible=effect.visible,
            target=effect.target or "current",
            metadata={"payload": payload, "legacy_effect": True},
        )
        item = self.runtime_io.send_module_output(
            request,
            turn=turn,
            slot=slot,
            interaction_metadata=interaction_metadata,
        )
        return item.delivery if item is not None else None

    def _payload_text(self, payload: Any) -> str:
        if isinstance(payload, str):
            return payload
        if isinstance(payload, dict):
            if payload.get("type") == "text":
                return str(payload.get("text") or "")
            if "text" in payload:
                return str(payload.get("text") or "")
            if "content" in payload:
                return str(payload.get("content") or "")
        return ""

    def _delivery_history_content(self, text: str, artifacts: list[ArtifactRef]) -> str:
        lines = [text] if text else []
        for artifact in artifacts:
            summary = artifact.summary or artifact.media_type or artifact.kind
            lines.append(f"[artifact:{artifact.artifact_id} {artifact.kind} {summary}]")
        return "\n\n".join(line for line in lines if line).strip()

    async def drain_background_tasks(self, *, include_task_worker: bool = True) -> None:
        while self._background_tasks:
            await asyncio.gather(*list(self._background_tasks), return_exceptions=True)
        if include_task_worker:
            await self.task_worker.drain()

    @property
    def background_task_count(self) -> int:
        return sum(1 for task in self._background_tasks if not task.done()) + self.task_worker.active_count

    def _submit_turn_task(self, *, core: LoadedCore, turn_id: str, metadata: Mapping[str, Any]) -> str | None:
        control_plane = getattr(self.session_runtime, "control_plane", None)
        if control_plane is None:
            return None
        control_plane.submit(
            ActionSpec(
                kind="agent.turn",
                payload={
                    "task_id": turn_id,
                    "owner_session_id": self.session_id,
                    "owner_turn_id": turn_id,
                    "core_id": core.core_id,
                    "notify_policy": "session",
                    "metadata": dict(metadata),
                },
                idempotency_key=f"turn:{turn_id}:submitted",
            ),
            source=ActionSource(
                actor="host.session_runtime",
                session_id=self.session_id,
                turn_id=turn_id,
                core_id=core.core_id,
                metadata=dict(metadata),
            ),
        )
        control_plane.mark_started(
            turn_id,
            source=ActionSource(
                actor="host.session_runtime",
                session_id=self.session_id,
                turn_id=turn_id,
                core_id=core.core_id,
                metadata=dict(metadata),
            ),
        )
        return turn_id

    def _complete_turn_task(self, turn_id: str, *, result_ref: str | None = None) -> None:
        control_plane = getattr(self.session_runtime, "control_plane", None)
        if control_plane is not None:
            control_plane.succeed(turn_id, result_ref=result_ref)

    def _finalize_interrupted_turn(
        self,
        turn_id: str,
        *,
        status: str,
        error: str,
        metadata: Mapping[str, Any],
    ) -> None:
        self.event_log.emit(f"turn.{status}", turn_id=turn_id, error=error, **dict(metadata))
        self.session_runtime.complete_turn(session_id=self.session_id, turn_id=turn_id, status=status, result_ref=turn_id)
        control_plane = getattr(self.session_runtime, "control_plane", None)
        if control_plane is None:
            return
        source = ActionSource(actor="host.session_runtime", session_id=self.session_id, turn_id=turn_id, metadata=dict(metadata))
        if status == "cancelled":
            control_plane.cancel(turn_id, summary=error, source=source)
        else:
            control_plane.fail(turn_id, error=error, summary=error, source=source)

    def _append_runtime_event(self, event: RuntimeEvent) -> None:
        self._append_runtime_events([event])

    def append_turn_runtime_event(self, event: RuntimeEvent) -> None:
        self._append_runtime_event(event)

    def _append_runtime_events(self, events: list[RuntimeEvent]) -> None:
        control_plane = getattr(self.session_runtime, "control_plane", None)
        if control_plane is not None:
            control_plane.record_events(events)

    def _ack_background_completion_claims(self, metadata: Mapping[str, Any]) -> None:
        claims = []
        raw_claims = metadata.get("completion_claims")
        if isinstance(raw_claims, list):
            claims.extend(item for item in raw_claims if isinstance(item, Mapping))
        if metadata.get("event_id") and metadata.get("completion_claim_id"):
            claims.append({"event_id": metadata.get("event_id"), "claim_id": metadata.get("completion_claim_id")})
        for item in claims:
            event_id = item.get("event_id")
            claim_id = item.get("claim_id")
            if event_id and claim_id:
                self.task_worker.ack_pending_event_id(str(event_id), claim_id=str(claim_id))

    def _sanitize_runtime_error(self, exc: Exception) -> str:
        message = str(exc).replace("\n", " ").strip()
        if len(message) > 500:
            message = f"{message[:500]}... [truncated]"
        return f"{exc.__class__.__name__}: {message}" if message else exc.__class__.__name__

    def _interaction_item_outbound_metadata(
        self,
        interaction_metadata: dict[str, Any],
        item: InteractionItem,
    ) -> dict[str, Any]:
        if item.delivery is not None:
            return self._background_outbound_metadata(interaction_metadata, [item.delivery])
        metadata = dict(interaction_metadata)
        for key in ("phase", "step_id", "tool_name", "tool_call_id", "is_error", "dispatch_status"):
            if item.metadata.get(key) is not None:
                metadata[key] = item.metadata[key]
        return metadata

    def _background_outbound_metadata(
        self,
        interaction_metadata: dict[str, Any],
        deliveries: list[InteractionDelivery],
    ) -> dict[str, Any]:
        metadata = dict(interaction_metadata)
        if not deliveries:
            return metadata
        delivery_metadata = deliveries[0].metadata
        route = delivery_metadata.get("route")
        if isinstance(route, dict):
            for key in ("session_id", "turn_id", "channel", "conversation_key", "source", "reply_to"):
                if route.get(key) is not None:
                    metadata.setdefault(key, route.get(key))
        for key in ("slot", "phase", "delivery_id", "kind", "history_policy", "delivery", "delivery_status", "background"):
            if delivery_metadata.get(key) is not None:
                metadata[key] = delivery_metadata[key]
        return metadata

    def _delivery_event_metadata(self, metadata: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in metadata.items() if key != "turn_id"}

    async def _call_slot(self, slot: SlotDefinition, ctx: Any) -> Any:
        outcome = await self.slot_runtime.invoke(SlotInvocation(slot=slot, context=ctx, phase=slot.kind))
        outcome.raise_for_error()
        return outcome.value

    def _normalize_context_items(
        self,
        items: list[ContextContribution | dict[str, Any]],
        *,
        default_placement: str = "pre_current_user",
    ) -> list[ContextContribution]:
        result: list[ContextContribution] = []
        for item in items:
            if isinstance(item, ContextContribution):
                if not item.placement:
                    item.placement = default_placement
                result.append(item)
            elif isinstance(item, dict):
                data = dict(item)
                data.setdefault("placement", default_placement)
                result.append(ContextContribution(**data))
        return result

    def _normalize_effect(self, value: EffectRequest | dict[str, Any]) -> EffectRequest:
        if isinstance(value, DeliverEffect):
            return EffectRequest(
                type="deliver",
                payload=value.payload,
                attachments=list(value.attachments),
                visible=value.visible,
                history_policy=value.history_policy,
                target=value.target,
            )
        if isinstance(value, EffectRequest):
            return value
        return EffectRequest(**value)
