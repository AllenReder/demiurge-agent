from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any, Literal, Mapping

from demiurge.runtime.completions import CompletionInbox
from demiurge.runtime.control import ActionSource, ActionSpec
from demiurge.runtime.session import SessionRuntime
from demiurge.runtime.slot_context import ModuleStateStores
from demiurge.runtime.tasks import RuntimeTaskWorker
from demiurge.sdk import AgentInput, InputEnvelope, TurnContext
from demiurge.storage import EventLog, StateStore
from demiurge.util import utc_id


TurnInterruptStatus = Literal["failed", "cancelled"]


@dataclass(frozen=True, slots=True)
class TurnLifecycleRequest:
    session_id: str
    core_id: str
    core_revision: str
    raw_text: str
    metadata: Mapping[str, Any] = field(default_factory=dict)
    attachments: tuple[Any, ...] = ()


@dataclass(slots=True)
class TurnLifecycle:
    session_id: str
    turn_id: str
    task_id: str | None
    input_envelope: InputEnvelope
    user_input: AgentInput
    turn: TurnContext
    state_stores: ModuleStateStores
    metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class TurnLifecycleCompletion:
    items: tuple[Any, ...] = ()
    agent_result: Any = None
    needs_user: bool = False
    result_ref: str | None = None


class TurnLifecycleRuntime:
    """Owns host turn lifecycle across event log, session store, and task ledger."""

    def __init__(
        self,
        *,
        home: Path,
        session_runtime: SessionRuntime,
        task_worker: RuntimeTaskWorker,
        event_log: EventLog,
    ) -> None:
        self.home = home
        self.session_runtime = session_runtime
        self.task_worker = task_worker
        self.event_log = event_log

    def begin(self, request: TurnLifecycleRequest) -> TurnLifecycle:
        metadata = dict(request.metadata)
        turn_id = utc_id("turn_")
        input_envelope = InputEnvelope(
            raw_text=request.raw_text,
            metadata=metadata,
            attachments=list(request.attachments),
        )
        user_input = AgentInput(content=request.raw_text, metadata=metadata)
        state_stores = ModuleStateStores(
            core=StateStore.core(self.home, request.core_id),
            session=StateStore.session(self.home, core_id=request.core_id, session_id=request.session_id),
        )
        turn = TurnContext(
            session_id=request.session_id,
            turn_id=turn_id,
            core_id=request.core_id,
            core_revision=request.core_revision,
            user_input=user_input,
            metadata=metadata,
        )

        self.event_log.emit(
            "turn.started",
            turn_id=turn_id,
            core_id=request.core_id,
            core_revision=request.core_revision,
            **metadata,
        )
        task_id = self._submit_turn_task(request=request, turn_id=turn_id, metadata=metadata)
        self.session_runtime.start_turn(session_id=request.session_id, turn_id=turn_id, task_id=task_id)
        self.event_log.emit("message.inbound", turn_id=turn_id, content=request.raw_text, **metadata)

        return TurnLifecycle(
            session_id=request.session_id,
            turn_id=turn_id,
            task_id=task_id,
            input_envelope=input_envelope,
            user_input=user_input,
            turn=turn,
            state_stores=state_stores,
            metadata=metadata,
        )

    def complete(self, lifecycle: TurnLifecycle, completion: TurnLifecycleCompletion) -> None:
        result_ref = completion.result_ref or lifecycle.turn_id
        self.event_log.emit(
            "turn.completed",
            turn_id=lifecycle.turn_id,
            items=[_serialize_item(item) for item in completion.items],
            agent_result=completion.agent_result,
            needs_user=completion.needs_user,
            **lifecycle.metadata,
        )
        if lifecycle.task_id is not None:
            self.session_runtime.control_plane.succeed(lifecycle.task_id, result_ref=result_ref)
        self.session_runtime.complete_turn(
            session_id=lifecycle.session_id,
            turn_id=lifecycle.turn_id,
            result_ref=result_ref,
        )
        CompletionInbox(self.task_worker).ack_from_metadata(lifecycle.metadata)

    def interrupt(self, lifecycle: TurnLifecycle, *, status: TurnInterruptStatus, error: str) -> None:
        self.event_log.emit(f"turn.{status}", turn_id=lifecycle.turn_id, error=error, **lifecycle.metadata)
        self.session_runtime.complete_turn(
            session_id=lifecycle.session_id,
            turn_id=lifecycle.turn_id,
            status=status,
            result_ref=lifecycle.turn_id,
        )
        if lifecycle.task_id is None:
            return
        source = ActionSource(
            actor="host.session_runtime",
            session_id=lifecycle.session_id,
            turn_id=lifecycle.turn_id,
            core_id=lifecycle.turn.core_id,
            metadata=dict(lifecycle.metadata),
        )
        if status == "cancelled":
            self.session_runtime.control_plane.cancel(lifecycle.task_id, summary=error, source=source)
        else:
            self.session_runtime.control_plane.fail(lifecycle.task_id, error=error, summary=error, source=source)

    def _submit_turn_task(
        self,
        *,
        request: TurnLifecycleRequest,
        turn_id: str,
        metadata: Mapping[str, Any],
    ) -> str | None:
        control_plane = getattr(self.session_runtime, "control_plane", None)
        if control_plane is None:
            return None
        source = ActionSource(
            actor="host.session_runtime",
            session_id=request.session_id,
            turn_id=turn_id,
            core_id=request.core_id,
            metadata=dict(metadata),
        )
        control_plane.submit(
            ActionSpec(
                kind="agent.turn",
                payload={
                    "task_id": turn_id,
                    "owner_session_id": request.session_id,
                    "owner_turn_id": turn_id,
                    "core_id": request.core_id,
                    "notify_policy": "session",
                    "metadata": dict(metadata),
                },
                idempotency_key=f"turn:{turn_id}:submitted",
            ),
            source=source,
        )
        control_plane.mark_started(turn_id, source=source)
        return turn_id


def _serialize_item(item: Any) -> Any:
    if is_dataclass(item) and not isinstance(item, type):
        return asdict(item)
    if isinstance(item, Mapping):
        return dict(item)
    return item
