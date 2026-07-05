from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from demiurge.core import LoadedCore, SlotDefinition
from demiurge.providers import ToolCall
from demiurge.runtime.delivery import (
    CONTENT_BLOCK_TYPES,
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
from demiurge.runtime.interactions import InteractionItem
from demiurge.runtime.session import SessionRuntime
from demiurge.runtime.slots import InputSlotRunRequest, ModuleInputBuilder, OutputSlotRunRequest
from demiurge.sdk import (
    AgentRunResult,
    AgentSpawnHandle,
    HistoryMessageSummary,
    InputContext,
    OutputContext,
    RawInput,
    StateProposal,
    ToolResult,
    TurnContext,
)
from demiurge.security.capabilities import CapabilityFacade
from demiurge.storage import StateStore
from demiurge.util import utc_id


@dataclass(slots=True)
class ModuleStateStores:
    core: StateStore
    session: StateStore


@dataclass(slots=True)
class SlotContextBuild:
    context: InputContext | OutputContext
    io_client: "ModuleIOClient"


class SlotContextHost(Protocol):
    @property
    def home(self) -> Path:
        ...

    @property
    def session_id(self) -> str:
        ...

    @property
    def workspace(self) -> str | None:
        ...

    @property
    def sessions(self) -> SessionRuntime:
        ...

    def emit_event(self, event_type: str, **payload: Any) -> dict[str, Any]:
        ...

    def commit_module_delivery_request(
        self,
        request: DeliveryRequest,
        *,
        turn: TurnContext,
        slot: SlotDefinition,
        interaction_metadata: dict[str, Any],
    ) -> InteractionItem | None:
        ...

    def schedule_interaction_item(
        self,
        item: InteractionItem,
        *,
        turn: TurnContext,
        interaction_metadata: dict[str, Any],
    ) -> None:
        ...

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
        ...

    async def run_child_agent(
        self,
        *,
        core_id: str,
        raw_input: str,
        parent_turn: TurnContext,
        parent_slot_path: str,
        context: list[str],
        input_slots: Any = None,
        output_slots: Any = None,
        use_bootstrap: bool = False,
        tools: Any = "all",
    ) -> AgentRunResult:
        ...

    def spawn_child_agent(
        self,
        *,
        core_id: str,
        raw_input: str,
        parent_turn: TurnContext,
        parent_slot_path: str,
        context: list[str],
        input_slots: Any = None,
        output_slots: Any = None,
        use_bootstrap: bool = False,
        tools: Any = "all",
    ) -> AgentSpawnHandle:
        ...


class RunnerSlotContextHost:
    """Adapter from SessionTurnStepRunner to SlotContextHost."""

    def __init__(self, runner: Any):
        self.runner = runner

    @property
    def home(self) -> Path:
        return self.runner.home

    @property
    def session_id(self) -> str:
        return self.runner.session_id

    @property
    def workspace(self) -> str | None:
        return self.runner.workspace

    @property
    def sessions(self) -> SessionRuntime:
        return self.runner.sessions

    def emit_event(self, event_type: str, **payload: Any) -> dict[str, Any]:
        return self.runner.event_log.emit(event_type, **payload)

    def commit_module_delivery_request(
        self,
        request: DeliveryRequest,
        *,
        turn: TurnContext,
        slot: SlotDefinition,
        interaction_metadata: dict[str, Any],
    ) -> InteractionItem | None:
        return self.runner.commit_module_delivery_request(
            request,
            turn=turn,
            slot=slot,
            interaction_metadata=interaction_metadata,
        )

    def schedule_interaction_item(
        self,
        item: InteractionItem,
        *,
        turn: TurnContext,
        interaction_metadata: dict[str, Any],
    ) -> None:
        self.runner.schedule_interaction_item(
            item,
            turn=turn,
            interaction_metadata=interaction_metadata,
        )

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
        return await self.runner.execute_tool(
            call,
            core=core,
            turn=turn,
            capability=capability,
            emit_event=emit_event,
            output_factory=output_factory,
        )

    async def run_child_agent(
        self,
        *,
        core_id: str,
        raw_input: str,
        parent_turn: TurnContext,
        parent_slot_path: str,
        context: list[str],
        input_slots: Any = None,
        output_slots: Any = None,
        use_bootstrap: bool = False,
        tools: Any = "all",
    ) -> AgentRunResult:
        return await self.runner._run_child_agent(
            core_id=core_id,
            raw_input=raw_input,
            parent_turn=parent_turn,
            parent_slot_path=parent_slot_path,
            context=context,
            input_slots=input_slots,
            output_slots=output_slots,
            use_bootstrap=use_bootstrap,
            tools=tools,
        )

    def spawn_child_agent(
        self,
        *,
        core_id: str,
        raw_input: str,
        parent_turn: TurnContext,
        parent_slot_path: str,
        context: list[str],
        input_slots: Any = None,
        output_slots: Any = None,
        use_bootstrap: bool = False,
        tools: Any = "all",
    ) -> AgentSpawnHandle:
        return self.runner._spawn_child_agent(
            core_id=core_id,
            raw_input=raw_input,
            parent_turn=parent_turn,
            parent_slot_path=parent_slot_path,
            context=context,
            input_slots=input_slots,
            output_slots=output_slots,
            use_bootstrap=use_bootstrap,
            tools=tools,
        )


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
        host: SlotContextHost,
        core: LoadedCore,
        turn: TurnContext,
        capability: CapabilityFacade,
        slot_path: str,
        output_factory: Callable[[SlotDefinition], "ModuleIOClient"],
    ):
        self.host = host
        self.core = core
        self.turn = turn
        self.capability = capability
        self.slot_path = slot_path
        self.output_factory = output_factory

    async def call(self, name: str, arguments: Mapping[str, Any] | None = None):
        self.capability.require(f"tool.call:{name}", slot_path=self.slot_path)
        return await self.host.execute_tool(
            ToolCall(name=name, arguments=dict(arguments or {})),
            core=self.core,
            turn=self.turn,
            capability=self.capability,
            emit_event=self.host.emit_event,
            output_factory=self.output_factory,
        )


class ModuleAgentsClient:
    def __init__(
        self,
        *,
        host: SlotContextHost,
        capability: CapabilityFacade,
        slot_path: str,
        turn: TurnContext,
    ):
        self.host = host
        self.capability = capability
        self.slot_path = slot_path
        self.turn = turn

    async def run(
        self,
        core_id: str,
        raw_input: str,
        *,
        context: str | list[str] | None = None,
        input_slots: Any = None,
        output_slots: Any = None,
        use_bootstrap: bool = False,
        tools: Any = "all",
    ) -> AgentRunResult:
        self.capability.require(f"agents.run:{core_id}", slot_path=self.slot_path)
        run_id = utc_id("agent_run_")
        self.host.emit_event(
            "agent_run.started",
            turn_id=self.turn.turn_id,
            slot=self.slot_path,
            agent_run_id=run_id,
            child_core_id=core_id,
        )
        result = await self.host.run_child_agent(
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
        self.host.emit_event(
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
        input_slots: Any = None,
        output_slots: Any = None,
        use_bootstrap: bool = False,
        tools: Any = "all",
    ) -> AgentSpawnHandle:
        self.capability.require(f"agents.spawn:{core_id}", slot_path=self.slot_path)
        return self.host.spawn_child_agent(
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
        envelope: Any,
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


class SlotContextRuntime:
    """Builds authored input/output slot SDK contexts behind a small Interface."""

    def __init__(self, host: SlotContextHost):
        self.host = host

    def build_input_context(self, request: InputSlotRunRequest, *, items: list[InteractionItem]) -> SlotContextBuild:
        io_client = self.module_io_client(
            request.slot,
            turn=request.turn,
            capability=request.capability,
            interaction_metadata=request.interaction_metadata,
            background=request.background,
            items=items,
        )
        ctx = InputContext(
            turn=request.turn,
            slot_id=request.slot.slot_id,
            slot_path=request.slot.relative_path,
            capability=request.capability,
            input=ModuleInputClient(
                raw_input=request.raw_input,
                builder=request.builder,
                writable=request.builder_writable,
                sender=io_client,
            ),
            history=ModuleHistoryClient(sessions=self.host.sessions, session_id=self.host.session_id),
            agents=ModuleAgentsClient(
                host=self.host,
                capability=request.capability,
                slot_path=request.slot.relative_path,
                turn=request.turn,
            ),
            state=ModuleStateClient(
                state_stores=request.state_stores,
                capability=request.capability,
                slot_path=request.slot.relative_path,
                turn_id=request.turn.turn_id,
                emit_event=self.host.emit_event,
            ),
            tools=ModuleToolClient(
                host=self.host,
                core=request.core,
                turn=request.turn,
                capability=request.capability,
                slot_path=request.slot.relative_path,
                output_factory=lambda slot: self.module_io_client(
                    slot,
                    turn=request.turn,
                    capability=request.capability,
                    interaction_metadata=request.interaction_metadata,
                    items=items,
                ),
            ),
            skills=ModuleSkillClient(
                envelope=request.envelope,
                capability=request.capability,
                slot_path=request.slot.relative_path,
                turn_id=request.turn.turn_id,
                emit_event=self.host.emit_event,
            ),
        )
        return SlotContextBuild(context=ctx, io_client=io_client)

    def build_output_context(self, request: OutputSlotRunRequest, *, items: list[InteractionItem]) -> SlotContextBuild:
        io_client = self.module_io_client(
            request.slot,
            turn=request.turn,
            capability=request.capability,
            interaction_metadata=request.interaction_metadata,
            background=request.background,
            items=items,
        )
        ctx = OutputContext(
            turn=request.turn,
            slot_id=request.slot.slot_id,
            slot_path=request.slot.relative_path,
            capability=request.capability,
            output=ModuleOutputClient(content=request.current_output, metadata=request.envelope.metadata, sender=io_client),
            history=ModuleHistoryClient(sessions=self.host.sessions, session_id=self.host.session_id),
            agents=ModuleAgentsClient(
                host=self.host,
                capability=request.capability,
                slot_path=request.slot.relative_path,
                turn=request.turn,
            ),
            state=ModuleStateClient(
                state_stores=request.state_stores,
                capability=request.capability,
                slot_path=request.slot.relative_path,
                turn_id=request.turn.turn_id,
                emit_event=self.host.emit_event,
            ),
            tools=ModuleToolClient(
                host=self.host,
                core=request.core,
                turn=request.turn,
                capability=request.capability,
                slot_path=request.slot.relative_path,
                output_factory=lambda slot: self.module_io_client(
                    slot,
                    turn=request.turn,
                    capability=request.capability,
                    interaction_metadata=request.interaction_metadata,
                    items=items,
                ),
            ),
            result=request.result_client,
        )
        return SlotContextBuild(context=ctx, io_client=io_client)

    def result_client(self, *, writable: bool) -> ModuleResultClient:
        return ModuleResultClient(
            home=self.host.home,
            session_id=self.host.session_id,
            workspace=self.host.workspace,
            writable=writable,
        )

    def module_io_client(
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
        commit = lambda request: self.host.commit_module_delivery_request(
            request,
            turn=turn,
            slot=slot,
            interaction_metadata=interaction_metadata,
        )
        schedule = lambda item: self.host.schedule_interaction_item(
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
            home=self.host.home,
            session_id=self.host.session_id,
            workspace=self.host.workspace,
            default_history_policy=slot.history_policy,
            default_write_history=default_write_history,
            allow_write_history=allow_write_history,
            commit=commit,
            schedule=schedule,
            route=route,
            background=background,
            items=items,
        )

    def _delivery_route_context(
        self,
        turn: TurnContext,
        slot: SlotDefinition,
        interaction_metadata: dict[str, Any],
    ) -> DeliveryRouteContext:
        return DeliveryRouteContext(
            session_id=self.host.session_id,
            turn_id=turn.turn_id,
            channel=interaction_metadata.get("channel"),
            conversation_key=interaction_metadata.get("conversation_key"),
            source=interaction_metadata.get("source"),
            reply_to=interaction_metadata.get("reply_to"),
            slot=slot.relative_path,
            metadata=dict(interaction_metadata),
        )
