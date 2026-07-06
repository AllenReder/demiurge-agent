from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from demiurge.core import LoadedCore
from demiurge.runtime.session import SessionRuntime
from demiurge.runtime.slots import SlotInvocation, SlotRuntime
from demiurge.sdk import BootstrapContext
from demiurge.security.capabilities import CapabilityFacade


class ModuleBootstrapClient:
    """Collects bootstrap context fragments from authored bootstrap slots."""

    def __init__(self, *, workspace: str | None = None) -> None:
        self.fragments: list[str] = []
        self.workspace = workspace or ""

    def add(self, text: str) -> None:
        content = str(text or "")
        if content.strip():
            self.fragments.append(content)


@dataclass(slots=True)
class BootstrapSlotRequest:
    session_id: str
    core: LoadedCore
    core_revision: str
    capability: CapabilityFacade
    workspace: str | None
    interaction_metadata: dict[str, Any]


class BootstrapSlotHost(Protocol):
    @property
    def session_runtime(self) -> SessionRuntime:
        ...

    @property
    def slot_runtime(self) -> SlotRuntime:
        ...

    def emit_event(self, event_type: str, **payload: Any) -> dict[str, Any]:
        ...


class RunnerBootstrapSlotHost:
    """Adapter from SessionTurnStepRunner to BootstrapSlotHost."""

    def __init__(self, runner: Any):
        self.runner = runner

    @property
    def session_runtime(self) -> SessionRuntime:
        return self.runner.session_runtime

    @property
    def slot_runtime(self) -> SlotRuntime:
        return self.runner.slot_runtime

    def emit_event(self, event_type: str, **payload: Any) -> dict[str, Any]:
        return self.runner.event_log.emit(event_type, **payload)


class BootstrapSlotRuntime:
    """Runs bootstrap slots and persists the session bootstrap snapshot."""

    def __init__(self, host: BootstrapSlotHost):
        self.host = host

    async def ensure(self, request: BootstrapSlotRequest) -> None:
        core = request.core
        if not core.bootstrap_enabled:
            return
        if self.host.session_runtime.bootstrap_context_exists(request.session_id):
            return

        pipeline = core.bootstrap_pipeline
        serial_slots = list(pipeline.serial) if pipeline is not None else []
        self.host.emit_event(
            "bootstrap.started",
            core_id=core.core_id,
            core_revision=request.core_revision,
            slots=[slot.slot_id for slot in serial_slots],
            **request.interaction_metadata,
        )
        fragments: list[str] = []
        try:
            for slot in serial_slots:
                self.host.emit_event(
                    "bootstrap.module.started",
                    core_id=core.core_id,
                    core_revision=request.core_revision,
                    slot=slot.relative_path,
                    kind="bootstrap",
                    **request.interaction_metadata,
                )
                client = ModuleBootstrapClient(workspace=request.workspace)
                ctx = BootstrapContext(
                    session_id=request.session_id,
                    core_id=core.core_id,
                    core_revision=request.core_revision,
                    workspace=request.workspace or "",
                    slot_id=slot.slot_id,
                    slot_path=slot.relative_path,
                    capability=request.capability,
                    bootstrap=client,
                )
                try:
                    outcome = await self.host.slot_runtime.invoke(
                        SlotInvocation(slot=slot, context=ctx, phase="bootstrap")
                    )
                    outcome.raise_for_error()
                    if outcome.value is not None:
                        self.host.emit_event(
                            "bootstrap.module.return_ignored",
                            core_id=core.core_id,
                            core_revision=request.core_revision,
                            slot=slot.relative_path,
                            kind="bootstrap",
                            **request.interaction_metadata,
                        )
                    fragments.extend(client.fragments)
                    self.host.emit_event(
                        "bootstrap.module.completed",
                        core_id=core.core_id,
                        core_revision=request.core_revision,
                        slot=slot.relative_path,
                        kind="bootstrap",
                        fragments=len(client.fragments),
                        chars=sum(len(fragment) for fragment in client.fragments),
                        **request.interaction_metadata,
                    )
                except Exception as exc:
                    self.host.emit_event(
                        "bootstrap.module.failed",
                        core_id=core.core_id,
                        core_revision=request.core_revision,
                        slot=slot.relative_path,
                        kind="bootstrap",
                        error=str(exc),
                        **request.interaction_metadata,
                    )
                    if slot.failure_policy == "hard":
                        raise
            content = "\n\n".join(fragments)
            self.host.session_runtime.write_bootstrap_context(request.session_id, content)
            self.host.emit_event(
                "bootstrap.completed",
                core_id=core.core_id,
                core_revision=request.core_revision,
                fragments=len(fragments),
                chars=len(content),
                **request.interaction_metadata,
            )
        except Exception as exc:
            self.host.emit_event(
                "bootstrap.failed",
                core_id=core.core_id,
                core_revision=request.core_revision,
                error=str(exc),
                **request.interaction_metadata,
            )
            raise
