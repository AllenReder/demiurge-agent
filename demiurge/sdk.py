from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from demiurge.runtime.delivery import ArtifactInput, ArtifactRef, ContentBlock, DeliveryHandle, DeliveryRequest


JsonValue = Any
# persist: write session history and model context; model_hidden: write visible
# history only; transient: write events/deliveries only.
DELIVERY_HISTORY_POLICIES = {"persist", "model_hidden", "transient"}
INPUT_HISTORY_POLICIES = {"persist", "transient"}
INPUT_SECTIONS = {"system", "user"}


@dataclass(slots=True)
class ContextContribution:
    """Additional turn-scoped context contributed by input modules."""

    type: str
    content: str | None = None
    key: str | None = None
    value: JsonValue | None = None
    priority: str = "normal"
    placement: str = "pre_current_user"


@dataclass(slots=True)
class InputEnvelope:
    raw_text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    attachments: list[JsonValue] = field(default_factory=list)
    activated_skills: list[str] = field(default_factory=list)


@dataclass(slots=True)
class OutputEnvelope:
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    attachments: list[ArtifactRef | Mapping[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class ToolResult:
    content: str
    data: JsonValue | None = None
    is_error: bool = False
    terminate: bool = False
    model_output: str | None = None
    display_output: str | None = None


@dataclass(slots=True)
class AgentInput:
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class TurnContext:
    session_id: str
    turn_id: str
    core_id: str
    core_revision: str
    user_input: AgentInput
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RawInput:
    text: str
    metadata: Mapping[str, Any] = field(default_factory=dict)
    attachments: tuple[JsonValue, ...] = ()


@dataclass(frozen=True, slots=True)
class HistoryMessageSummary:
    message_id: str
    role: str
    content: str
    turn_id: str | None
    created_at: str
    step_id: str | None = None
    tool_call_id: str | None = None
    tool_calls: tuple[Mapping[str, Any], ...] = ()
    visible: bool = True
    model_visible: bool = True
    tool_name: str | None = None
    is_error: bool | None = None


@dataclass(frozen=True, slots=True)
class AgentDeliverySummary:
    kind: str
    text: str
    history_policy: str
    visible: bool = True


@dataclass(frozen=True, slots=True)
class AgentToolSummary:
    name: str
    content: str
    is_error: bool = False


@dataclass(frozen=True, slots=True)
class AgentRunResult:
    content: str
    core_id: str
    session_id: str
    turn_id: str
    result: JsonValue | None = None
    deliveries: tuple[AgentDeliverySummary, ...] = ()
    tools: tuple[AgentToolSummary, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AgentSpawnHandle:
    task_id: str
    core_id: str
    session_id: str
    status: str = "running"


class CapabilityClient(Protocol):
    def can(self, capability: str, *, slot_path: str | None = None) -> bool:
        ...

    def require(self, capability: str, *, slot_path: str | None = None) -> None:
        ...


class ScopedModuleStateClient(Protocol):
    def get(self, target: str, default: JsonValue | None = None) -> JsonValue:
        ...

    def set(self, target: str, value: JsonValue) -> JsonValue:
        ...

    def merge(self, target: str, value: Mapping[str, Any]) -> JsonValue:
        ...

    def append(self, target: str, value: JsonValue) -> JsonValue:
        ...

    def snapshot(self) -> Mapping[str, Any]:
        ...


class ModuleStateClient(Protocol):
    core: ScopedModuleStateClient
    session: ScopedModuleStateClient


@dataclass(slots=True)
class InputContext:
    """Context object injected into agent/input module process(ctx)."""

    turn: TurnContext
    slot_id: str
    slot_path: str
    capability: CapabilityClient
    input: Any
    history: Any = None
    agents: Any = None
    state: Any = None
    tools: Any = None
    skills: Any = None


@dataclass(slots=True)
class BootstrapContext:
    """Context object injected into agent/bootstrap module process(ctx)."""

    session_id: str
    core_id: str
    core_revision: str
    workspace: str
    slot_id: str
    slot_path: str
    capability: CapabilityClient
    bootstrap: Any


@dataclass(slots=True)
class OutputContext:
    """Context object injected into agent/output module process(ctx)."""

    turn: TurnContext
    slot_id: str
    slot_path: str
    capability: CapabilityClient
    output: Any
    history: Any = None
    agents: Any = None
    state: Any = None
    tools: Any = None
    result: Any = None


@dataclass(slots=True)
class ToolContext:
    turn: TurnContext
    slot_id: str
    slot_path: str
    capability: CapabilityClient
    output: Any = None
    workspace: Any = None
