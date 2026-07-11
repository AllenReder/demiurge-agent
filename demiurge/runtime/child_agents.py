from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from demiurge.core import LoadedCore, SlotDefinition
from demiurge.providers import ToolCall
from demiurge.runtime.interactions import InteractionInbound
from demiurge.runtime.tasks import (
    RuntimeTaskConflictError,
    RuntimeTaskContext,
    RuntimeTaskOutcome,
)
from demiurge.runtime.slots import ResolvedPhaseSlots
from demiurge.sdk import (
    AgentDeliverySummary,
    AgentInput,
    AgentRunResult,
    AgentSpawnHandle,
    AgentToolSummary,
    ToolResult,
    TurnContext,
)
from demiurge.security.capabilities import CapabilityFacade
from demiurge.util import utc_id


CHILD_AGENT_DEFAULT_INPUT_SLOTS = ("base_input",)
CHILD_AGENT_DEFAULT_OUTPUT_SLOTS = ("base_output",)
CHILD_AGENT_ALL_SLOTS = "all"
CHILD_AGENT_ALL_TOOLS = "all"
CHILD_AGENT_NO_TOOLS = "none"

ChildSlotRequest = str | Sequence[str] | None
ChildToolRequest = str | Sequence[str] | None


@dataclass(slots=True)
class ChildAgentRunRequest:
    core_id: str
    raw_input: str
    parent_turn: TurnContext
    parent_slot_path: str
    context: list[str] = field(default_factory=list)
    input_slots: ChildSlotRequest = None
    output_slots: ChildSlotRequest = None
    use_bootstrap: bool = False
    tools: ChildToolRequest = CHILD_AGENT_ALL_TOOLS
    session_id: str | None = None


@dataclass(slots=True)
class ChildAgentSpawnRequest:
    core_id: str
    raw_input: str
    parent_turn: TurnContext
    parent_slot_path: str
    context: list[str] = field(default_factory=list)
    input_slots: ChildSlotRequest = None
    output_slots: ChildSlotRequest = None
    use_bootstrap: bool = False
    tools: ChildToolRequest = CHILD_AGENT_ALL_TOOLS
    notify_on_complete: bool = True
    session_id: str | None = None
    resolved_child_tools: "ResolvedChildAgentTools | None" = None


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


class ChildAgentHost(Protocol):
    @property
    def version_store(self) -> Any:
        ...

    @property
    def core_loader(self) -> Any:
        ...

    @property
    def tool_runtime(self) -> Any:
        ...

    @property
    def task_worker(self) -> Any:
        ...

    @property
    def session_runtime(self) -> Any:
        ...

    @property
    def sessions(self) -> Any:
        ...

    def emit_event(self, event_type: str, **payload: Any) -> dict[str, Any]:
        ...

    def core_revision(self, core: LoadedCore) -> str:
        ...

    def resolve_model_name(self, core: LoadedCore) -> str:
        ...

    def create_child_runner(self, *, core_id: str, session_id: str) -> Any:
        ...


class RunnerChildAgentHost:
    """Adapter from SessionTurnStepRunner to ChildAgentHost."""

    def __init__(self, runner: Any):
        self.runner = runner

    @property
    def version_store(self) -> Any:
        return self.runner.version_store

    @property
    def core_loader(self) -> Any:
        return self.runner.core_loader

    @property
    def tool_runtime(self) -> Any:
        return self.runner.tool_runtime

    @property
    def task_worker(self) -> Any:
        return self.runner.task_worker

    @property
    def session_runtime(self) -> Any:
        return self.runner.session_runtime

    @property
    def sessions(self) -> Any:
        return self.runner.sessions

    def emit_event(self, event_type: str, **payload: Any) -> dict[str, Any]:
        return self.runner.emit_turn_event(event_type, **payload)

    def core_revision(self, core: LoadedCore) -> str:
        return self.runner._core_revision(core)

    def resolve_model_name(self, core: LoadedCore) -> str:
        return self.runner._resolve_model_name(core)

    def create_child_runner(self, *, core_id: str, session_id: str) -> Any:
        return self.runner.__class__(
            home=self.runner.home,
            version_store=self.runner.version_store,
            core_loader=self.runner.core_loader,
            provider=self.runner.provider,
            tool_runtime=self.runner.tool_runtime,
            core_id=core_id,
            session_id=session_id,
            model_override=self.runner.model_override,
            model_resolver=self.runner.model_resolver,
            provider_name=self.runner.provider_name,
            workspace=self.runner.workspace,
            show_system_prompt=self.runner.show_system_prompt,
            runtime_timezone=self.runner.runtime_timezone,
            task_worker=self.runner.task_worker,
            session_runtime=self.runner.session_runtime,
            slot_runtime=self.runner.slot_runtime,
            interaction_router=self.runner.interaction_router,
            prepare_live_core=self.runner.prepare_live_core_callback,
        )


class ChildAgentRuntime:
    """Runs and spawns child agents behind a dedicated child lifecycle interface."""

    def __init__(self, host: ChildAgentHost):
        self.host = host

    def resolve_slots(
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
            input=self._resolve_phase_slots(core, "input", input_slots),
            output=self._resolve_phase_slots(core, "output", output_slots),
            use_bootstrap=use_bootstrap,
        )

    def resolve_slots_for_core_id(
        self,
        core_id: str,
        *,
        input_slots: ChildSlotRequest,
        output_slots: ChildSlotRequest,
        use_bootstrap: bool,
    ) -> ResolvedChildAgentSlots:
        core = self.host.core_loader.load(self.host.version_store.active_core_path(core_id))
        return self.resolve_slots(
            core,
            input_slots=input_slots,
            output_slots=output_slots,
            use_bootstrap=use_bootstrap,
        )

    def _resolve_phase_slots(
        self,
        core: LoadedCore,
        kind: str,
        requested: ChildSlotRequest,
    ) -> ResolvedPhaseSlots:
        pipeline = core.input_pipeline if kind == "input" else core.output_pipeline
        default_ids = CHILD_AGENT_DEFAULT_INPUT_SLOTS if kind == "input" else CHILD_AGENT_DEFAULT_OUTPUT_SLOTS
        requested_ids = self.normalize_slot_request(kind, requested, default_ids=default_ids)
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

    def normalize_slot_request(
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

    def slot_request_metadata(
        self,
        kind: str,
        requested: ChildSlotRequest,
        *,
        default_ids: tuple[str, ...],
    ) -> str | list[str]:
        normalized = self.normalize_slot_request(kind, requested, default_ids=default_ids)
        if normalized == CHILD_AGENT_ALL_SLOTS:
            return CHILD_AGENT_ALL_SLOTS
        return list(normalized)

    def resolve_tools(self, core: LoadedCore, requested: ChildToolRequest) -> ResolvedChildAgentTools:
        requested_tools = self.normalize_tool_request(requested)
        registry_entries = self.host.tool_runtime.registry_for(core)
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

    async def resolve_tools_for_core_id_prepared(
        self,
        core_id: str,
        requested: ChildToolRequest,
        *,
        session_id: str,
    ) -> ResolvedChildAgentTools:
        core = self.host.core_loader.load(self.host.version_store.active_core_path(core_id))
        return await self.resolve_tools_prepared(core, requested, session_id=session_id)

    async def resolve_tools_prepared(
        self,
        core: LoadedCore,
        requested: ChildToolRequest,
        *,
        session_id: str,
    ) -> ResolvedChildAgentTools:
        normalized = self.normalize_tool_request(requested)
        if normalized != CHILD_AGENT_NO_TOOLS:
            await self.prepare_tool_registry(core, session_id=session_id)
        return self.resolve_tools(core, normalized)

    async def prepare_tool_registry(self, core: LoadedCore, *, session_id: str) -> None:
        if not core.mcp_servers:
            return
        turn = TurnContext(
            session_id=session_id,
            turn_id=utc_id("turn_child_tools_"),
            core_id=core.core_id,
            core_revision=self.host.core_revision(core),
            user_input=AgentInput(content=""),
            metadata={},
        )
        await self.host.tool_runtime.prepare_for_turn(
            core,
            turn,
            emit_event=lambda event_type, **payload: self.host.emit_event(
                event_type,
                **{**payload, "session_id": session_id},
            ),
        )

    def requested_tools_for_core_id(self, core_id: str, requested: ChildToolRequest) -> str | list[str]:
        normalized = self.normalize_tool_request(requested)
        if isinstance(normalized, str):
            return normalized
        core = self.host.core_loader.load(self.host.version_store.active_core_path(core_id))
        available_tool_ids = {entry.name for entry in self.host.tool_runtime.registry_for(core)}
        missing = [tool_id for tool_id in normalized if tool_id not in available_tool_ids]
        if missing:
            unresolved_without_mcp_shape = [tool_id for tool_id in missing if "__" not in tool_id]
            if not core.mcp_servers or unresolved_without_mcp_shape:
                raise ValueError(f"unknown child tool id: {(unresolved_without_mcp_shape or missing)[0]}")
        return list(normalized)

    def normalize_tool_request(self, requested: ChildToolRequest) -> tuple[str, ...] | str:
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

    async def run_child(self, request: ChildAgentRunRequest) -> AgentRunResult:
        child_session_id = request.session_id or utc_id("session_child_")
        child_runner = self.host.create_child_runner(core_id=request.core_id, session_id=child_session_id)
        await child_runner.prepare_live_core()
        child_core_path = self.host.version_store.active_core_path(request.core_id)
        child_core = self.host.core_loader.load(child_core_path)
        child_slots = self.resolve_slots(
            child_core,
            input_slots=request.input_slots,
            output_slots=request.output_slots,
            use_bootstrap=request.use_bootstrap,
        )
        child_tools = await self.resolve_tools_prepared(
            child_core,
            request.tools,
            session_id=child_session_id,
        )
        self.host.session_runtime.update_session(
            child_session_id,
            core_id=child_core.core_id,
            core_revision=self.host.core_revision(child_core),
            provider=child_runner.provider_name,
            model=self.host.resolve_model_name(child_core),
            touch=False,
        )
        child_slot_metadata = child_slots.to_metadata()
        child_tool_metadata = {"requested": child_tools.requested, "resolved": child_tools.resolved}
        child_metadata = {
            "delegation_depth": int(request.parent_turn.metadata.get("delegation_depth") or 0) + 1,
            "parent_session_id": request.parent_turn.session_id,
            "parent_turn_id": request.parent_turn.turn_id,
            "parent_slot": request.parent_slot_path,
            "child_agent_slots": child_slot_metadata,
            "child_agent_tools": child_tool_metadata,
        }
        if child_tools.tool_policy:
            child_metadata["tool_policy"] = child_tools.tool_policy
        result = await child_runner.run_turn(
            request.raw_input,
            core_path=child_core_path,
            interaction=InteractionInbound(
                channel="agent",
                text=request.raw_input,
                source=request.parent_turn.session_id,
                metadata=child_metadata,
            ),
            injected_system_context=list(request.context),
            input_phase_slots=child_slots.input,
            output_phase_slots=child_slots.output,
            use_bootstrap=child_slots.use_bootstrap,
        )
        await child_runner.background_tasks.drain(include_runtime_tasks=False)
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
                "parent_turn_id": request.parent_turn.turn_id,
                "parent_slot": request.parent_slot_path,
                "needs_user": needs_user,
                "child_agent_slots": child_slot_metadata,
                "child_agent_tools": child_tool_metadata,
            },
        )

    def spawn_child(self, request: ChildAgentSpawnRequest) -> AgentSpawnHandle:
        self.resolve_slots_for_core_id(
            request.core_id,
            input_slots=request.input_slots,
            output_slots=request.output_slots,
            use_bootstrap=request.use_bootstrap,
        )
        requested_tools = (
            request.resolved_child_tools.requested
            if request.resolved_child_tools is not None
            else self.requested_tools_for_core_id(request.core_id, request.tools)
        )
        requested_slot_metadata = {
            "input_slots": self.slot_request_metadata(
                "input",
                request.input_slots,
                default_ids=CHILD_AGENT_DEFAULT_INPUT_SLOTS,
            ),
            "output_slots": self.slot_request_metadata(
                "output",
                request.output_slots,
                default_ids=CHILD_AGENT_DEFAULT_OUTPUT_SLOTS,
            ),
            "use_bootstrap": request.use_bootstrap,
        }
        session_id = request.session_id or utc_id("session_child_")

        async def run_task(ctx: RuntimeTaskContext) -> RuntimeTaskOutcome:
            self.host.emit_event(
                "agent_spawn.started",
                session_id=request.parent_turn.session_id,
                turn_id=request.parent_turn.turn_id,
                slot=request.parent_slot_path,
                task_id=ctx.task_id,
                child_core_id=request.core_id,
                child_session_id=session_id,
            )
            try:
                result = await self.run_child(
                    ChildAgentRunRequest(
                        core_id=request.core_id,
                        raw_input=request.raw_input,
                        parent_turn=request.parent_turn,
                        parent_slot_path=request.parent_slot_path,
                        context=list(request.context),
                        input_slots=request.input_slots,
                        output_slots=request.output_slots,
                        use_bootstrap=request.use_bootstrap,
                        tools=request.tools,
                        session_id=session_id,
                    )
                )
                child_slot_metadata = result.metadata.get("child_agent_slots")
                child_tool_metadata = result.metadata.get("child_agent_tools")
                summary = result.content or f"child agent {request.core_id} completed"
                ctx.update_metadata(
                    {
                        "child_core_id": request.core_id,
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
                    summary = summary or f"child agent {request.core_id} needs user input"
                    ctx.mark_blocked(summary, metadata={"needs_user": True})
                self.host.emit_event(
                    event_name,
                    session_id=request.parent_turn.session_id,
                    turn_id=request.parent_turn.turn_id,
                    slot=request.parent_slot_path,
                    task_id=ctx.task_id,
                    child_core_id=request.core_id,
                    child_session_id=session_id,
                    child_turn_id=result.turn_id,
                )
                return RuntimeTaskOutcome(
                    summary=summary,
                    result_ref=f"session:{session_id}:{result.turn_id}",
                    metadata={
                        "child_core_id": request.core_id,
                        "child_session_id": session_id,
                        "child_turn_id": result.turn_id,
                        "needs_user": needs_user,
                        "resolved_child_agent_slots": child_slot_metadata,
                        "resolved_child_agent_tools": child_tool_metadata,
                    },
                )
            except Exception as exc:
                self.host.emit_event(
                    "agent_spawn.failed",
                    session_id=request.parent_turn.session_id,
                    turn_id=request.parent_turn.turn_id,
                    slot=request.parent_slot_path,
                    task_id=ctx.task_id,
                    child_core_id=request.core_id,
                    child_session_id=session_id,
                    error=str(exc),
                )
                raise

        try:
            record = self.host.task_worker.start_task(
                kind="agent.spawn",
                owner_session_id=request.parent_turn.session_id,
                owner_turn_id=request.parent_turn.turn_id,
                source_tool="agents.spawn",
                task_factory=run_task,
                write_scope=f"agent-session:{session_id}",
                notify_on_complete=request.notify_on_complete,
                metadata={
                    "child_core_id": request.core_id,
                    "child_session_id": session_id,
                    "parent_slot": request.parent_slot_path,
                    "requested_child_agent_slots": requested_slot_metadata,
                    "requested_child_agent_tools": requested_tools,
                },
            )
        except RuntimeTaskConflictError as exc:
            self.host.emit_event(
                "agent_spawn.rejected",
                session_id=request.parent_turn.session_id,
                turn_id=request.parent_turn.turn_id,
                slot=request.parent_slot_path,
                child_core_id=request.core_id,
                child_session_id=session_id,
                error=str(exc),
            )
            return AgentSpawnHandle(task_id="", core_id=request.core_id, session_id=session_id, status="failed")
        return AgentSpawnHandle(task_id=record.task_id, core_id=request.core_id, session_id=session_id)

    async def handle_delegate_task(
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
            for task in self.host.task_worker.list_tasks(owner_session_id=turn.session_id, kind="agent.spawn")
            if task.owner_turn_id == turn.turn_id
        ]
        if len(total_children) >= 4:
            return ToolResult(content="delegation limit exceeded: max_children=4", is_error=True)
        running_children = self.host.task_worker.list_tasks(
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
        context = self.delegation_context(context_mode, session_id=turn.session_id)
        child_tools_request = call.arguments.get("tools", CHILD_AGENT_ALL_TOOLS)
        child_session_id = utc_id("session_child_")
        try:
            child_tools = await self.resolve_tools_for_core_id_prepared(
                child_core_id,
                child_tools_request,
                session_id=child_session_id,
            )
            handle = self.spawn_child(
                ChildAgentSpawnRequest(
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
            )
        except ValueError as exc:
            return ToolResult(content=str(exc), is_error=True)
        payload = {"task_id": handle.task_id}
        return ToolResult(content=json.dumps(payload, ensure_ascii=False), data=payload)

    def delegation_context(self, context_mode: str, *, session_id: str) -> list[str]:
        if context_mode == "isolated":
            return []
        messages = self.host.sessions.history_for_context(session_id)[-12:]
        if not messages:
            return []
        transcript = "\n".join(f"{message.role}: {message.content}" for message in messages if message.content.strip())
        return [f"Parent session fork context:\n{transcript}"] if transcript.strip() else []

    def _turn_result_needs_user(self, result: Any) -> bool:
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
