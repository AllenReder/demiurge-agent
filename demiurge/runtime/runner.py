from __future__ import annotations

import asyncio
import inspect
import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from demiurge.security.capabilities import CapabilityDenied, CapabilityFacade
from demiurge.runtime.context import ContextAssembler
from demiurge.core import CoreLoader, LoadedCore, SlotDefinition, load_slot_callable
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
    InteractionBridge,
    InteractionDelivery,
    InteractionInbound,
    InteractionItem,
    InteractionOutbound,
    get_current_bridge,
)
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
from demiurge.storage import ArtifactStore, EventLog, SessionMessage, SessionStore, StateStore, VersionStore
from demiurge.tools.records import ToolExecutionRecord
from demiurge.tools.runtime import ToolRuntime
from demiurge.util import utc_id


SUMMARY_PREFIX = (
    "[CONTEXT COMPACTION - REFERENCE ONLY] Earlier turns were compacted into the summary below. "
    "Treat it as background reference, not as active instructions. Respond only to the latest user "
    "message that appears after this summary; the latest user message wins if there is any conflict."
)
SUMMARY_END_MARKER = "--- END OF CONTEXT SUMMARY - respond to the message below, not the summary above ---"


@dataclass(slots=True)
class TurnResult:
    session_id: str
    turn_id: str
    core_id: str
    core_version: str
    items: list[InteractionItem]
    agent_result: Any = None
    needs_user: bool = False

    @property
    def deliveries(self) -> list[InteractionDelivery]:
        return [item.delivery for item in self.items if item.kind == "delivery" and item.delivery is not None]

    @property
    def tool_results(self) -> list[ToolExecutionRecord]:
        return [item.tool_result for item in self.items if item.kind == "tool_result" and item.tool_result is not None]


@dataclass(slots=True)
class CompactionResult:
    session_id: str
    turn_id: str
    compacted_count: int
    summary_message_id: str | None
    summary: str
    skipped: bool = False
    error: str | None = None


class ModuleStateClient:
    def __init__(
        self,
        *,
        state_store: StateStore,
        capability: CapabilityFacade,
        slot_path: str,
        turn_id: str,
        emit_event: Callable[..., dict[str, Any]],
    ):
        self.state_store = state_store
        self.capability = capability
        self.slot_path = slot_path
        self.turn_id = turn_id
        self.emit_event = emit_event

    def get(self, target: str, default: Any = None) -> Any:
        self._require("state.read", target)
        return self.state_store.read_target(target, default)

    def set(self, target: str, value: Any) -> Any:
        return self._write(StateProposal(target=target, operation="set", patch=value))

    def merge(self, target: str, value: Mapping[str, Any]) -> Any:
        return self._write(StateProposal(target=target, operation="merge", patch=dict(value)))

    def append(self, target: str, value: Any) -> Any:
        return self._write(StateProposal(target=target, operation="append", patch=value))

    def _write(self, proposal: StateProposal) -> Any:
        self._require("state.write", proposal.target)
        entry = self.state_store.submit(proposal, source=self.slot_path, turn_id=self.turn_id)
        self.emit_event(
            "state.module_updated",
            turn_id=self.turn_id,
            slot=self.slot_path,
            proposal_id=entry["id"],
            target=proposal.target,
            operation=proposal.operation,
        )
        return self.state_store.read_target(proposal.target)

    def _require(self, capability_name: str, target: str) -> None:
        scoped = f"{capability_name}:{target}"
        if self.capability.can(scoped, slot_path=self.slot_path):
            self.capability.require(scoped, slot_path=self.slot_path)
            return
        self.capability.require(capability_name, slot_path=self.slot_path)


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
        interaction_bridge: InteractionBridge | None,
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
        self.interaction_bridge = interaction_bridge
        self.emit_event = emit_event
        self.items = items

    async def call(self, name: str, arguments: Mapping[str, Any] | None = None):
        self.capability.require(f"tool.call:{name}", slot_path=self.slot_path)
        return await self.tool_runtime.execute(
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
                interaction_bridge=self.interaction_bridge,
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
    ) -> AgentSpawnHandle:
        self.capability.require(f"agents.spawn:{core_id}", slot_path=self.slot_path)
        return self.parent._spawn_child_agent(
            core_id=core_id,
            raw_input=raw_input,
            parent_turn=self.turn,
            parent_slot_path=self.slot_path,
            context=self._normalize_context(context),
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


class ModuleInputBuilder:
    def __init__(self) -> None:
        self.fragments: list[dict[str, str]] = []

    def add(
        self,
        section: str,
        content: str,
        *,
        history_policy: str | None = None,
        default_history_policy: str = "persist",
    ) -> None:
        section = section.strip()
        if section not in INPUT_SECTIONS:
            raise ValueError(f"invalid input section: {section}")
        policy = history_policy if history_policy is not None else ("transient" if section == "system" else default_history_policy)
        if policy not in INPUT_HISTORY_POLICIES:
            raise ValueError(f"invalid input history_policy: {policy}")
        if section == "system" and policy != "transient":
            raise ValueError("system input fragments cannot be persisted")
        text = str(content or "").strip()
        if not text:
            return
        self.fragments.append({"section": section, "content": text, "history_policy": policy})

    def section_text(self, section: str, *, persisted_only: bool = False) -> str:
        parts = [
            item["content"]
            for item in self.fragments
            if item["section"] == section and (not persisted_only or item["history_policy"] == "persist")
        ]
        return "\n\n".join(parts).strip()


class ModuleInputClient:
    def __init__(self, *, raw_input: RawInput, builder: ModuleInputBuilder, writable: bool, sender: "ModuleIOClient"):
        self.raw_input = raw_input
        self._builder = builder
        self._writable = writable
        self._sender = sender

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
    def __init__(self, *, session_store: SessionStore, session_id: str):
        self.session_store = session_store
        self.session_id = session_id

    def recent_messages(self, limit: int, roles: list[str] | tuple[str, ...] | set[str] | None = None) -> list[HistoryMessageSummary]:
        allowed = set(roles or {"user", "tool", "assistant"}) & {"user", "tool", "assistant"}
        if limit <= 0 or not allowed:
            return []
        messages = [
            message
            for message in self.session_store.read_messages(self.session_id)
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
        commit: Callable[[DeliveryRequest], InteractionItem | None],
        schedule: Callable[[InteractionItem], None],
        route: DeliveryRouteContext | None = None,
        background: bool = False,
        items: list[InteractionItem] | None = None,
    ):
        self.home = home
        self.session_id = session_id
        self.workspace = Path(workspace or ".").resolve()
        self.session_root = (home / "sessions" / session_id).resolve()
        self.default_history_policy = default_history_policy
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
        history_policy: str | None = None,
        delivery: str = "immediate",
        visible: bool = True,
        metadata: Mapping[str, Any] | None = None,
    ) -> DeliveryHandle:
        """Commit a host-mediated delivery request.

        The runner commits history/artifacts immediately. ``delivery`` controls
        only when the resulting channel item is sent.
        """
        resolved_policy = history_policy or self.default_history_policy
        if delivery not in DELIVERY_MODES:
            raise ValueError(f"invalid delivery mode: {delivery}")
        normalized = self._normalize_blocks(blocks)
        request_metadata = dict(metadata or {})
        request_metadata["delivery"] = delivery
        if self.background:
            request_metadata["background"] = True
        if self.route is not None:
            request_metadata.setdefault("route", self._route_metadata())
        request = DeliveryRequest(
            delivery_id=utc_id("delivery_"),
            kind=kind,
            blocks=normalized,
            history_policy=resolved_policy,
            delivery=delivery,
            visible=visible,
            target="current",
            metadata=request_metadata,
        )
        item = self.commit(request)
        if item is not None:
            self.items.append(item)
            if delivery == "immediate":
                self.schedule(item)
            else:
                self.slot_end_items.append(item)
        return DeliveryHandle(delivery_id=request.delivery_id)

    def flush_slot_end(self) -> None:
        for item in self.slot_end_items:
            self.schedule(item)
        self.slot_end_items.clear()

    def send_text(
        self,
        text: str,
        *,
        history_policy: str | None = None,
        delivery: str = "immediate",
        visible: bool = True,
        delivery_metadata: Mapping[str, Any] | None = None,
    ) -> DeliveryHandle:
        return self.send(
            ContentBlock(type="text", text=text),
            history_policy=history_policy,
            delivery=delivery,
            visible=visible,
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
            history_policy="transient",
            delivery="immediate",
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
            history_policy="transient",
            delivery="immediate",
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
        history_policy: str | None = None,
        delivery: str = "immediate",
        visible: bool = True,
        delivery_metadata: Mapping[str, Any] | None = None,
    ) -> DeliveryHandle:
        return self._send_artifact_block(
            "image",
            source,
            caption=caption,
            media_type=media_type,
            summary=summary,
            artifact_metadata=artifact_metadata,
            history_policy=history_policy,
            delivery=delivery,
            visible=visible,
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
        history_policy: str | None = None,
        delivery: str = "immediate",
        visible: bool = True,
        delivery_metadata: Mapping[str, Any] | None = None,
    ) -> DeliveryHandle:
        return self._send_artifact_block(
            "audio",
            source,
            caption=caption,
            media_type=media_type,
            summary=summary,
            artifact_metadata=artifact_metadata,
            history_policy=history_policy,
            delivery=delivery,
            visible=visible,
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
        history_policy: str | None = None,
        delivery: str = "immediate",
        visible: bool = True,
        delivery_metadata: Mapping[str, Any] | None = None,
    ) -> DeliveryHandle:
        return self._send_artifact_block(
            "video",
            source,
            caption=caption,
            media_type=media_type,
            summary=summary,
            artifact_metadata=artifact_metadata,
            history_policy=history_policy,
            delivery=delivery,
            visible=visible,
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
        history_policy: str | None = None,
        delivery: str = "immediate",
        visible: bool = True,
        delivery_metadata: Mapping[str, Any] | None = None,
    ) -> DeliveryHandle:
        return self._send_artifact_block(
            "file",
            source,
            caption=caption,
            media_type=media_type,
            summary=summary,
            artifact_metadata=artifact_metadata,
            history_policy=history_policy,
            delivery=delivery,
            visible=visible,
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
        history_policy: str | None,
        delivery: str,
        visible: bool,
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
            history_policy=history_policy,
            delivery=delivery,
            visible=visible,
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
        message = self.runner.session_store.append_message(
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
        interaction_bridge: InteractionBridge | None,
    ) -> tuple[SessionMessage, list[InteractionItem]]:
        message = self.runner.session_store.append_message(
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
            interaction_bridge=interaction_bridge,
        )
        return message, [item]

    def send_tool_result(
        self,
        *,
        turn: TurnContext,
        step_id: str,
        record: ToolExecutionRecord,
        interaction_metadata: dict[str, Any],
        interaction_bridge: InteractionBridge | None,
    ) -> InteractionItem:
        content = self.runner._truncate_model_content(self.runner._tool_result_model_content(record.result))
        message = self.runner.session_store.append_message(
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
            interaction_bridge=interaction_bridge,
        )
        return item

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
        self.session_root = (home / "sessions" / session_id).resolve()
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
        self.session_store = SessionStore(home)
        self.context_assembler = ContextAssembler()
        self.event_log = EventLog(home, self.session_id)
        self.runtime_io = RuntimeIO(self)
        self.history: list[LLMMessage] = []
        self.display_turns: list[dict[str, Any]] = []
        self._session_started_ids: set[str] = set()
        self._background_tasks: set[asyncio.Task[Any]] = set()
        self._interaction_queues: dict[
            str,
            asyncio.Queue[tuple[InteractionItem, TurnContext, dict[str, Any], InteractionBridge, str]],
        ] = {}
        self._interaction_queue_tasks: dict[str, asyncio.Task[Any]] = {}
        self._ensure_current_session()

    async def run_turn(
        self,
        text: str,
        *,
        core_path: Path | None = None,
        interaction: InteractionInbound | None = None,
        injected_system_context: list[str] | None = None,
        input_slot_ids: list[str] | tuple[str, ...] | None = None,
        output_slot_ids: list[str] | tuple[str, ...] | None = None,
    ) -> TurnResult:
        core = self.core_loader.load(core_path or self.version_store.active_core_path(self.core_id))
        interaction_metadata = self._interaction_metadata(interaction)
        self._resolve_session_for_interaction(core, interaction_metadata)
        input_slots_override = self._resolve_phase_slots(core, "input", input_slot_ids)
        output_slots_override = self._resolve_phase_slots(core, "output", output_slot_ids)
        capability = CapabilityFacade(core)
        if not self._session_started:
            self.event_log.emit("session.started", core_id=core.core_id, core_version=core.version, **interaction_metadata)
            self._session_started_ids.add(self.session_id)
        await self._ensure_bootstrap_context(core, capability, interaction_metadata=interaction_metadata)

        turn_id = utc_id("turn_")
        input_envelope = InputEnvelope(raw_text=text, metadata=interaction_metadata)
        user_input = AgentInput(content=text, metadata=interaction_metadata)
        state_store = StateStore(self.home, core.core_id)
        state = state_store.read()
        turn = TurnContext(
            session_id=self.session_id,
            turn_id=turn_id,
            core_id=core.core_id,
            core_version=core.version,
            user_input=user_input,
            state=state,
            metadata=interaction_metadata,
        )

        self.event_log.emit(
            "turn.started",
            turn_id=turn_id,
            core_id=core.core_id,
            core_version=core.version,
            **interaction_metadata,
        )
        self.event_log.emit("message.inbound", turn_id=turn_id, content=text, **interaction_metadata)

        user_text, persisted_user_text, context, input_items = await self._run_input_slots(
            core,
            turn,
            capability,
            input_envelope,
            state_store,
            interaction_metadata=interaction_metadata,
            interaction_bridge=get_current_bridge(),
            injected_system_context=injected_system_context or [],
            serial_slots=input_slots_override,
        )
        turn.user_input = AgentInput(content=user_text, metadata=interaction_metadata)
        self.event_log.emit("message.received", turn_id=turn_id, content=user_text, **interaction_metadata)
        if persisted_user_text:
            self.runtime_io.send_user(
                turn_id=turn_id,
                content=persisted_user_text,
                interaction_metadata=interaction_metadata,
            )
        turn_messages: list[LLMMessage] = [LLMMessage(role="user", content=user_text)]
        tool_records: list[ToolExecutionRecord] = []
        items: list[InteractionItem] = list(input_items)
        await self.tool_runtime.prepare_for_turn(core, turn, emit_event=self.event_log.emit)
        available_tools = self.tool_runtime.definitions_for(core)

        final_output = ""
        needs_user = False
        max_model_steps = core.manifest.runtime.max_model_steps
        for step_index in range(1, max_model_steps + 1):
            step_id = f"{turn_id}_step_{step_index}"
            self.event_log.emit(
                "step.started",
                turn_id=turn_id,
                step_id=step_id,
                tools=[tool.name for tool in available_tools],
                **interaction_metadata,
            )
            request = LLMRequest(
                model=self._resolve_model_name(core),
                messages=self._build_messages(core, context, turn_messages, turn_id=turn_id, step_id=step_id),
                tools=available_tools,
                metadata={"turn_id": turn_id, "step_id": step_id},
            )
            response = await self.provider.complete(request)
            if response.tool_calls:
                assistant_step_message = LLMMessage(
                    role="assistant",
                    content=response.content,
                    tool_calls=response.tool_calls,
                    persist=False,
                )
                turn_messages.append(assistant_step_message)
                persisted_assistant, interim_items = await self.runtime_io.send_assistant_step(
                    turn=turn,
                    step_id=step_id,
                    content=response.content,
                    tool_calls=response.tool_calls,
                    interaction_metadata=interaction_metadata,
                    interaction_bridge=get_current_bridge(),
                )
                items.extend(interim_items)
                self.event_log.emit(
                    "actions.requested",
                    turn_id=turn_id,
                    step_id=step_id,
                    actions=[asdict(call) for call in response.tool_calls],
                    **interaction_metadata,
                )
                terminated = False
                for call in response.tool_calls:
                    tool_items: list[InteractionItem] = []
                    result = await self.tool_runtime.execute(
                        call,
                        core=core,
                        turn=turn,
                        capability=capability,
                        emit_event=self.event_log.emit,
                        output_factory=lambda slot: self._module_io_client(
                            slot,
                            turn=turn,
                            capability=capability,
                            interaction_metadata=interaction_metadata,
                            interaction_bridge=get_current_bridge(),
                            items=tool_items,
                        ),
                    )
                    items.extend(tool_items)
                    record = ToolExecutionRecord(call=call, result=result)
                    tool_records.append(record)
                    turn_messages.append(
                        LLMMessage(
                            role="tool",
                            name=call.name,
                            tool_call_id=call.id,
                            content=self._truncate_model_content(self._tool_result_model_content(result)),
                            persist=False,
                        )
                    )
                    self.event_log.emit(
                        "action.result",
                        turn_id=turn_id,
                        step_id=step_id,
                        tool_name=call.name,
                        tool_call_id=call.id,
                        content=result.content,
                        model_output=result.model_output,
                        display_output=result.display_output,
                        data=result.data,
                        is_error=result.is_error,
                        terminate=result.terminate,
                        **interaction_metadata,
                    )
                    items.append(
                        self.runtime_io.send_tool_result(
                            turn=turn,
                            step_id=step_id,
                            record=record,
                            interaction_metadata=interaction_metadata,
                            interaction_bridge=get_current_bridge(),
                        )
                    )
                    if result.terminate:
                        final_output = result.content
                        needs_user = bool(isinstance(result.data, dict) and result.data.get("needs_user"))
                        turn_messages.append(LLMMessage(role="assistant", content=final_output))
                        self.event_log.emit(
                            "message.completed",
                            turn_id=turn_id,
                            content=final_output,
                            needs_user=needs_user,
                            **interaction_metadata,
                        )
                        terminated = True
                        break
                if terminated:
                    break
                continue

            final_output = response.content
            turn_messages.append(LLMMessage(role="assistant", content=final_output))
            self.event_log.emit("message.completed", turn_id=turn_id, content=final_output, **interaction_metadata)
            break
        else:
            final_output = (
                "The provider did not produce a final assistant message within "
                f"the configured step budget of {max_model_steps}."
            )
            turn_messages.append(LLMMessage(role="assistant", content=final_output))
            self.event_log.emit(
                "message.completed",
                turn_id=turn_id,
                content=final_output,
                is_error=True,
                **interaction_metadata,
            )

        result_client = self._module_result_client(writable=True)
        output_items = await self._run_output_slots(
            core,
            turn,
            capability,
            current_output=final_output,
            tool_records=tool_records,
            state_store=state_store,
            interaction_metadata=interaction_metadata,
            interaction_bridge=get_current_bridge(),
            result_client=result_client,
            serial_slots=output_slots_override,
        )
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
        return TurnResult(
            session_id=self.session_id,
            turn_id=turn_id,
            core_id=core.core_id,
            core_version=core.version,
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
        if self.session_store.bootstrap_context_exists(self.session_id):
            return

        self.event_log.emit(
            "bootstrap.started",
            core_id=core.core_id,
            core_version=core.version,
            slots=[slot.slot_id for slot in core.bootstrap_pipeline.serial],
            **interaction_metadata,
        )
        fragments: list[str] = []
        try:
            for slot in core.bootstrap_pipeline.serial:
                self.event_log.emit(
                    "bootstrap.module.started",
                    core_id=core.core_id,
                    core_version=core.version,
                    slot=slot.relative_path,
                    kind="bootstrap",
                    **interaction_metadata,
                )
                client = ModuleBootstrapClient(workspace=self.workspace)
                ctx = BootstrapContext(
                    session_id=self.session_id,
                    core_id=core.core_id,
                    core_version=core.version,
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
                            core_version=core.version,
                            slot=slot.relative_path,
                            kind="bootstrap",
                            **interaction_metadata,
                        )
                    fragments.extend(client.fragments)
                    self.event_log.emit(
                        "bootstrap.module.completed",
                        core_id=core.core_id,
                        core_version=core.version,
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
                        core_version=core.version,
                        slot=slot.relative_path,
                        kind="bootstrap",
                        error=str(exc),
                        **interaction_metadata,
                    )
                    if slot.failure_policy == "hard":
                        raise
            content = "\n\n".join(fragments)
            self.session_store.write_bootstrap_context(self.session_id, content)
            self.event_log.emit(
                "bootstrap.completed",
                core_id=core.core_id,
                core_version=core.version,
                fragments=len(fragments),
                chars=len(content),
                **interaction_metadata,
            )
        except Exception as exc:
            self.event_log.emit(
                "bootstrap.failed",
                core_id=core.core_id,
                core_version=core.version,
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
    ) -> list[LLMMessage]:
        assembled = self.context_assembler.assemble(
            core=core,
            context=context,
            session_history=[
                message
                for message in self.session_store.history_for_context(self.session_id)
                if message.turn_id != turn_id
            ],
            current_turn_messages=turn_messages,
            bootstrap_context=self.session_store.read_bootstrap_context(self.session_id),
            compaction_summary=self.session_store.latest_compaction_summary(self.session_id),
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

    def _build_skill_index(self, core: LoadedCore) -> str:
        return self.context_assembler._build_skill_index(core)

    def _resolve_model_name(self, core: LoadedCore) -> str:
        if self.model_resolver:
            return self.model_resolver(core.manifest.model)
        if self.model_override:
            return self.model_override
        return core.manifest.model.model_name or "fake/demo"

    def start_new_session(
        self,
        *,
        channel: str | None = None,
        conversation_key: str | None = None,
        source: str | None = None,
        reply_to: str | None = None,
    ) -> str:
        core = self.core_loader.load(self.version_store.active_core_path(self.core_id))
        record = self.session_store.create_session(
            core_id=core.core_id,
            core_version=core.version,
            channel=channel,
            conversation_key=conversation_key,
            workspace=self.workspace,
            provider=self.provider_name,
            model=self._resolve_model_name(core),
            metadata={key: value for key, value in {"source": source, "reply_to": reply_to}.items() if value is not None},
        )
        self._switch_session(record.session_id, emit_resumed=False)
        self.event_log.emit(
            "session.created",
            core_id=core.core_id,
            core_version=core.version,
            channel=channel,
            conversation_key=conversation_key,
        )
        return record.session_id

    def resume_session(self, session_id: str) -> None:
        self._switch_session(session_id, emit_resumed=True)

    async def compact_session(self, *, focus: str | None = None, protect_last_n: int = 6) -> CompactionResult:
        core = self.core_loader.load(self.version_store.active_core_path(self.core_id))
        turn_id = utc_id("compact_")
        self.event_log.emit("session.compaction.started", turn_id=turn_id, focus=focus)
        try:
            messages = [
                message
                for message in self.session_store.history_for_context(self.session_id)
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
            summary_message = self.session_store.write_compaction_summary(
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
        _, created = self.session_store.ensure_session(
            self.session_id,
            core_id=core.core_id,
            core_version=core.version,
            workspace=self.workspace,
            provider=self.provider_name,
            model=self._resolve_model_name(core),
        )
        self.event_log = EventLog(self.home, self.session_id)
        self.event_log.emit(
            "session.created" if created else "session.resumed",
            core_id=core.core_id,
            core_version=core.version,
        )
        self.history = self._session_history_messages()

    def _switch_session(self, session_id: str, *, emit_resumed: bool) -> None:
        if not self.session_store.exists(session_id):
            raise FileNotFoundError(f"session not found: {session_id}")
        self.session_id = session_id
        self.event_log = EventLog(self.home, self.session_id)
        self.history = self._session_history_messages()
        if emit_resumed:
            record = self.session_store.get(session_id)
            self.event_log.emit(
                "session.resumed",
                core_id=record.core_id,
                core_version=record.core_version,
                channel=record.channel,
                conversation_key=record.conversation_key,
            )

    def _interaction_metadata(self, interaction: InteractionInbound | None) -> dict[str, Any]:
        if interaction is None:
            return {}
        return {
            key: value
            for key, value in {
                "channel": interaction.channel,
                "source": interaction.source,
                "reply_to": interaction.reply_to,
                "conversation_key": interaction.conversation_key,
                **dict(interaction.metadata or {}),
            }.items()
            if value is not None
        }

    def _resolve_session_for_interaction(self, core: LoadedCore, interaction_metadata: dict[str, Any]) -> None:
        channel = interaction_metadata.get("channel")
        conversation_key = interaction_metadata.get("conversation_key")
        if not channel:
            return
        if not conversation_key:
            if self.session_store.can_bind_current_session(
                self.session_id,
                channel=str(channel),
                conversation_key=None,
            ):
                self.session_store.update_session(
                    self.session_id,
                    core_id=core.core_id,
                    core_version=core.version,
                    channel=str(channel),
                    conversation_key=None,
                    metadata={
                        key: value
                        for key, value in interaction_metadata.items()
                        if key not in {"channel", "conversation_key"}
                    },
                )
            return
        existing = self.session_store.resolve_interaction_session(
            core_id=core.core_id,
            channel=str(channel),
            conversation_key=str(conversation_key),
        )
        if existing:
            if existing != self.session_id:
                self._switch_session(existing, emit_resumed=True)
            return
        if self.session_store.can_bind_current_session(
            self.session_id,
            channel=str(channel),
            conversation_key=str(conversation_key),
        ):
            self.session_store.update_session(
                self.session_id,
                core_id=core.core_id,
                core_version=core.version,
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

    def _tool_result_model_content(self, result: ToolResult) -> str:
        return result.model_output if result.model_output is not None else result.content

    def _truncate_model_content(self, content: str) -> str:
        if len(content) > 4000:
            return f"{content[:4000]}\n...[truncated {len(content) - 4000} chars]"
        return content

    def _session_history_messages(self) -> list[LLMMessage]:
        messages: list[LLMMessage] = []
        for message in self.session_store.history_for_context(self.session_id):
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
        interaction_bridge: InteractionBridge | None,
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
            interaction_bridge=interaction_bridge,
        )
        return ModuleIOClient(
            home=self.home,
            session_id=self.session_id,
            workspace=self.workspace,
            default_history_policy=slot.history_policy,
            commit=commit,
            schedule=schedule,
            route=route,
            background=background,
            items=items,
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
        interaction_bridge: InteractionBridge | None,
    ) -> None:
        if item.dispatch_status != "pending":
            return
        metadata = self._interaction_item_outbound_metadata(interaction_metadata, item)
        bridge = interaction_bridge or get_current_bridge()
        channel = metadata.get("channel") or interaction_metadata.get("channel")
        if bridge is None or not channel:
            return
        item.set_dispatch_status("scheduled")
        self._enqueue_interaction_item(
            item,
            turn=turn,
            metadata=metadata,
            interaction_bridge=bridge,
            channel=str(channel),
        )

    def _schedule_slot_end_delivery_items(
        self,
        items: list[InteractionItem],
        *,
        turn: TurnContext,
        interaction_metadata: dict[str, Any],
        interaction_bridge: InteractionBridge | None,
    ) -> None:
        for item in items:
            self._schedule_interaction_item(
                item,
                turn=turn,
                interaction_metadata=interaction_metadata,
                interaction_bridge=interaction_bridge,
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
        )
        result = await child_runner.run_turn(raw_input, injected_system_context=context)
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
            metadata={"parent_turn_id": parent_turn.turn_id, "parent_slot": parent_slot_path},
        )

    def _spawn_child_agent(
        self,
        *,
        core_id: str,
        raw_input: str,
        parent_turn: TurnContext,
        parent_slot_path: str,
        context: list[str],
    ) -> AgentSpawnHandle:
        job_id = utc_id("agent_job_")
        session_id = utc_id("session_child_")

        async def run_job() -> None:
            self.event_log.emit(
                "agent_spawn.started",
                turn_id=parent_turn.turn_id,
                slot=parent_slot_path,
                job_id=job_id,
                child_core_id=core_id,
                child_session_id=session_id,
            )
            try:
                await self._run_child_agent(
                    core_id=core_id,
                    raw_input=raw_input,
                    parent_turn=parent_turn,
                    parent_slot_path=parent_slot_path,
                    context=context,
                    session_id=session_id,
                )
                self.event_log.emit(
                    "agent_spawn.completed",
                    turn_id=parent_turn.turn_id,
                    slot=parent_slot_path,
                    job_id=job_id,
                    child_core_id=core_id,
                    child_session_id=session_id,
                )
            except Exception as exc:
                self.event_log.emit(
                    "agent_spawn.failed",
                    turn_id=parent_turn.turn_id,
                    slot=parent_slot_path,
                    job_id=job_id,
                    child_core_id=core_id,
                    child_session_id=session_id,
                    error=str(exc),
                )

        task = asyncio.create_task(run_job())
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return AgentSpawnHandle(job_id=job_id, core_id=core_id, session_id=session_id)

    async def _run_input_slots(
        self,
        core: LoadedCore,
        turn: TurnContext,
        capability: CapabilityFacade,
        envelope: InputEnvelope,
        state_store: StateStore,
        *,
        interaction_metadata: dict[str, Any],
        interaction_bridge: InteractionBridge | None,
        injected_system_context: list[str],
        serial_slots: list[SlotDefinition] | None = None,
    ) -> tuple[str, str, list[ContextContribution], list[InteractionItem]]:
        builder = ModuleInputBuilder()
        raw_input = RawInput(
            text=envelope.raw_text,
            metadata=dict(envelope.metadata),
            attachments=tuple(envelope.attachments),
        )
        contributions: list[ContextContribution] = [
            ContextContribution(type="instruction", content=content, placement="system_context")
            for content in injected_system_context
            if content.strip()
        ]
        items: list[InteractionItem] = []
        activated: set[str] = set()
        parallel_slots = [] if serial_slots is not None else core.input_pipeline.parallel
        current_serial_slots = serial_slots or core.input_pipeline.serial
        for slot in parallel_slots:
            parallel_envelope = InputEnvelope(
                raw_text=envelope.raw_text,
                metadata=dict(envelope.metadata),
                attachments=list(envelope.attachments),
            )
            task = asyncio.create_task(
                self._run_async_input_slot(
                    slot,
                    core=core,
                    turn=turn,
                    capability=capability,
                    envelope=parallel_envelope,
                    raw_input=raw_input,
                    builder=builder,
                    state_store=state_store,
                    interaction_metadata=interaction_metadata,
                    interaction_bridge=interaction_bridge,
                )
            )
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)
            self.event_log.emit("module.async_scheduled", turn_id=turn.turn_id, slot=slot.relative_path, kind="input")
        for slot in current_serial_slots:
            items.extend(
                await self._run_input_slot(
                    slot,
                    core=core,
                    turn=turn,
                    capability=capability,
                    envelope=envelope,
                    raw_input=raw_input,
                    builder=builder,
                    builder_writable=True,
                    state_store=state_store,
                    interaction_metadata=interaction_metadata,
                    interaction_bridge=interaction_bridge,
                    activated=activated,
                    contributions=contributions,
                )
            )
        system_text = builder.section_text("system")
        if system_text:
            contributions.append(ContextContribution(type="instruction", content=system_text, placement="system_context"))
        user_text = builder.section_text("user")
        if not user_text:
            raise RuntimeError("input pipeline did not produce a user message")
        return user_text, builder.section_text("user", persisted_only=True), contributions, items

    async def _run_async_input_slot(
        self,
        slot: SlotDefinition,
        *,
        core: LoadedCore,
        turn: TurnContext,
        capability: CapabilityFacade,
        envelope: InputEnvelope,
        raw_input: RawInput,
        builder: ModuleInputBuilder,
        state_store: StateStore,
        interaction_metadata: dict[str, Any],
        interaction_bridge: InteractionBridge | None,
    ) -> None:
        items = await self._run_input_slot(
            slot,
            core=core,
            turn=turn,
            capability=capability,
            envelope=envelope,
            raw_input=raw_input,
            builder=builder,
            builder_writable=False,
            state_store=state_store,
            interaction_metadata=interaction_metadata,
            interaction_bridge=interaction_bridge,
            activated=set(),
            contributions=[],
            background=True,
        )
        await self._flush_pending_background_items(
            items,
            turn=turn,
            interaction_metadata=interaction_metadata,
            interaction_bridge=interaction_bridge,
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
        state_store: StateStore,
        interaction_metadata: dict[str, Any],
        interaction_bridge: InteractionBridge | None,
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
            interaction_bridge=interaction_bridge,
            background=background,
            items=items,
        )
        ctx = InputContext(
            turn=turn,
            slot_id=slot.slot_id,
            slot_path=slot.relative_path,
            capability=capability,
            input=ModuleInputClient(raw_input=raw_input, builder=builder, writable=builder_writable, sender=io_client),
            history=ModuleHistoryClient(session_store=self.session_store, session_id=self.session_id),
            agents=ModuleAgentsClient(
                parent=self,
                capability=capability,
                slot_path=slot.relative_path,
                turn=turn,
                interaction_metadata=interaction_metadata,
                emit_event=self.event_log.emit,
            ),
            state=ModuleStateClient(
                state_store=state_store,
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
                interaction_bridge=interaction_bridge,
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
                interaction_bridge=interaction_bridge,
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
        state_store: StateStore,
        interaction_metadata: dict[str, Any],
        interaction_bridge: InteractionBridge | None,
        result_client: ModuleResultClient,
        serial_slots: list[SlotDefinition] | None = None,
    ) -> list[InteractionItem]:
        items: list[InteractionItem] = []
        envelope = OutputEnvelope(content=current_output, metadata=interaction_metadata)
        parallel_slots = [] if serial_slots is not None else core.output_pipeline.parallel
        current_serial_slots = serial_slots or core.output_pipeline.serial
        for slot in parallel_slots:
            task = asyncio.create_task(
                self._run_async_output_slot(
                    slot,
                    core=core,
                    turn=turn,
                    capability=capability,
                    envelope=envelope,
                    current_output=current_output,
                    tool_records=tool_records,
                    state_store=state_store,
                    interaction_metadata=interaction_metadata,
                    interaction_bridge=interaction_bridge,
                    result_client=result_client.fork(writable=False),
                )
            )
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)
            self.event_log.emit(
                "module.async_scheduled",
                turn_id=turn.turn_id,
                slot=slot.relative_path,
                kind="output",
            )
        for slot in current_serial_slots:
            items.extend(
                await self._run_output_slot(
                    slot,
                    core=core,
                    turn=turn,
                    capability=capability,
                    envelope=envelope,
                    current_output=current_output,
                    tool_records=tool_records,
                    state_store=state_store,
                    interaction_metadata=interaction_metadata,
                    interaction_bridge=interaction_bridge,
                    result_client=result_client,
                )
            )
        return items

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
        state_store: StateStore,
        interaction_metadata: dict[str, Any],
        interaction_bridge: InteractionBridge | None,
        result_client: ModuleResultClient,
        background: bool = False,
    ) -> list[InteractionItem]:
        items: list[InteractionItem] = []
        io_client = self._module_io_client(
            slot,
            turn=turn,
            capability=capability,
            interaction_metadata=interaction_metadata,
            interaction_bridge=interaction_bridge,
            background=background,
            items=items,
        )
        ctx = OutputContext(
            turn=turn,
            slot_id=slot.slot_id,
            slot_path=slot.relative_path,
            capability=capability,
            output=ModuleOutputClient(content=current_output, metadata=envelope.metadata, sender=io_client),
            history=ModuleHistoryClient(session_store=self.session_store, session_id=self.session_id),
            agents=ModuleAgentsClient(
                parent=self,
                capability=capability,
                slot_path=slot.relative_path,
                turn=turn,
                interaction_metadata=interaction_metadata,
                emit_event=self.event_log.emit,
            ),
            state_slice=state_store.read(),
            state=ModuleStateClient(
                state_store=state_store,
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
                interaction_bridge=interaction_bridge,
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
                interaction_bridge=interaction_bridge,
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

    async def _run_async_output_slot(
        self,
        slot: SlotDefinition,
        *,
        core: LoadedCore,
        turn: TurnContext,
        capability: CapabilityFacade,
        envelope: OutputEnvelope,
        current_output: str,
        tool_records: list[ToolExecutionRecord],
        state_store: StateStore,
        interaction_metadata: dict[str, Any],
        interaction_bridge: InteractionBridge | None,
        result_client: ModuleResultClient,
    ) -> None:
        items = await self._run_output_slot(
            slot,
            core=core,
            turn=turn,
            capability=capability,
            envelope=envelope,
            current_output=current_output,
            tool_records=tool_records,
            state_store=state_store,
            interaction_metadata=interaction_metadata,
            interaction_bridge=interaction_bridge,
            result_client=result_client,
            background=True,
        )
        await self._flush_pending_background_items(
            items,
            turn=turn,
            interaction_metadata=interaction_metadata,
            interaction_bridge=interaction_bridge,
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
        interaction_bridge: InteractionBridge,
        channel: str,
    ) -> None:
        key = self._delivery_route_key(metadata)
        queue = self._interaction_queues.setdefault(key, asyncio.Queue())
        queue.put_nowait((item, turn, metadata, interaction_bridge, channel))
        task = self._interaction_queue_tasks.get(key)
        if task is None or task.done():
            task = asyncio.create_task(self._drain_interaction_queue(key))
            self._interaction_queue_tasks[key] = task
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)

    async def _drain_interaction_queue(self, key: str) -> None:
        queue = self._interaction_queues[key]
        try:
            while not queue.empty():
                item, turn, metadata, interaction_bridge, channel = await queue.get()
                outbound = InteractionOutbound(
                    channel=channel,
                    items=[item],
                    session_id=self.session_id,
                    turn_id=turn.turn_id,
                    metadata=metadata,
                )
                try:
                    await interaction_bridge.deliver(outbound)
                    item.set_dispatch_status("delivered")
                except Exception as exc:
                    item.metadata["dispatch_error"] = str(exc)
                    if item.delivery is not None:
                        item.delivery.metadata = {
                            **dict(item.delivery.metadata),
                            "delivery_error": str(exc),
                        }
                    item.set_dispatch_status("failed")
                    self.event_log.emit(
                        "delivery.failed",
                        turn_id=turn.turn_id,
                        reason="bridge_deliver_failed",
                        error=str(exc),
                        **self._delivery_event_metadata(metadata),
                    )
                finally:
                    queue.task_done()
        finally:
            if queue.empty():
                self._interaction_queues.pop(key, None)
                if self._interaction_queue_tasks.get(key) is asyncio.current_task():
                    self._interaction_queue_tasks.pop(key, None)

    async def _flush_pending_background_items(
        self,
        items: list[InteractionItem],
        *,
        turn: TurnContext,
        interaction_metadata: dict[str, Any],
        interaction_bridge: InteractionBridge | None,
    ) -> None:
        for item in items:
            if item.dispatch_status != "pending":
                continue
            metadata = self._interaction_item_outbound_metadata(interaction_metadata, item)
            bridge = interaction_bridge or get_current_bridge()
            channel = metadata.get("channel") or interaction_metadata.get("channel")
            if bridge is None or not channel:
                item.set_dispatch_status("failed")
                self.event_log.emit(
                    "delivery.failed",
                    turn_id=turn.turn_id,
                    reason="no_active_interaction_bridge",
                    **self._delivery_event_metadata(metadata),
                )
                continue
            item.set_dispatch_status("scheduled")
            self._enqueue_interaction_item(
                item,
                turn=turn,
                metadata=metadata,
                interaction_bridge=bridge,
                channel=str(channel),
            )

    async def _handle_effects(
        self,
        effects: list[EffectRequest | dict[str, Any]],
        *,
        core: LoadedCore,
        turn: TurnContext,
        capability: CapabilityFacade,
        slot: SlotDefinition,
        state_store: StateStore,
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
                elif effect.type == "state_proposal":
                    capability.require("state.propose", slot_path=slot.relative_path)
                    proposal = self._normalize_state_proposal(effect.proposal)
                    entry = state_store.submit(proposal, source=slot.relative_path, turn_id=turn.turn_id)
                    self.event_log.emit("state.proposal", turn_id=turn.turn_id, proposal_id=entry["id"])
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
                    result = await self.tool_runtime.execute(
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
            summary = artifact.summary or artifact.media_type or artifact.kind
            if block.text:
                fallback_lines.append(str(block.text))
            fallback_lines.append(f"[artifact:{artifact.artifact_id} {artifact.kind} {summary}]")
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

        fallback_text = "\n\n".join(line for line in fallback_lines if line).strip()
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
            **dict(request.metadata),
        }
        content = fallback_text
        message_id = None
        if history_policy != "transient":
            message = self.session_store.append_message(
                self.session_id,
                role="assistant",
                content=content,
                turn_id=turn.turn_id,
                visible=request.visible,
                model_visible=history_policy == "persist",
                interaction_metadata=interaction_metadata,
                metadata=metadata,
            )
            message_id = message.id
            self.event_log.emit(
                "message.persisted",
                turn_id=turn.turn_id,
                message_id=message.id,
                role=message.role,
                kind=message.kind,
                **interaction_metadata,
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
                data["resolved_path"] = str((self.home / "sessions" / self.session_id / path).resolve())
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

    async def drain_background_tasks(self) -> None:
        while self._background_tasks:
            await asyncio.gather(*list(self._background_tasks), return_exceptions=True)

    @property
    def background_task_count(self) -> int:
        return sum(1 for task in self._background_tasks if not task.done())

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

    def _delivery_route_key(self, metadata: dict[str, Any]) -> str:
        return "|".join(
            str(metadata.get(key) or "")
            for key in ("channel", "conversation_key", "source", "reply_to", "session_id")
        )

    def _delivery_event_metadata(self, metadata: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in metadata.items() if key != "turn_id"}

    async def _call_slot(self, slot: SlotDefinition, ctx: Any) -> Any:
        func = load_slot_callable(slot)
        value = func(ctx)
        if inspect.isawaitable(value):
            return await value
        return value

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

    def _normalize_state_proposal(self, value: StateProposal | dict[str, Any] | None) -> StateProposal:
        if value is None:
            raise ValueError("state_proposal effect requires proposal")
        if isinstance(value, StateProposal):
            return value
        return StateProposal(**value)
