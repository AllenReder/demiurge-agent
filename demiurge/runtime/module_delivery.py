from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any, Protocol

from demiurge.core import SlotDefinition
from demiurge.runtime.delivery import (
    CONTENT_BLOCK_TYPES,
    DELIVERY_MODES,
    ArtifactRef,
    ContentBlock,
    DeliveryRequest,
    artifact_input_to_dict,
)
from demiurge.runtime.host_work import delivery_work_enqueued_event
from demiurge.runtime.interactions import InteractionDelivery
from demiurge.runtime.session import SessionRuntime
from demiurge.runtime.store import RuntimeEvent
from demiurge.sdk import TurnContext
from demiurge.storage import ArtifactStore


class ModuleDeliveryHost(Protocol):
    @property
    def home(self) -> Path:
        ...

    @property
    def session_id(self) -> str:
        ...

    @property
    def session_runtime(self) -> SessionRuntime:
        ...

    def emit_event(self, event_type: str, **payload: Any) -> dict[str, Any]:
        ...

    def append_runtime_event(self, event: RuntimeEvent) -> None:
        ...

    def append_runtime_events(self, events: list[RuntimeEvent]) -> None:
        ...


class RunnerModuleDeliveryHost:
    """Adapter from SessionTurnStepRunner to ModuleDeliveryHost."""

    def __init__(self, runner: Any):
        self.runner = runner

    @property
    def home(self) -> Path:
        return self.runner.home

    @property
    def session_id(self) -> str:
        return self.runner.session_id

    @property
    def session_runtime(self) -> SessionRuntime:
        return self.runner.session_runtime

    def emit_event(self, event_type: str, **payload: Any) -> dict[str, Any]:
        return self.runner.event_log.emit(event_type, **payload)

    def append_runtime_event(self, event: RuntimeEvent) -> None:
        self.runner._append_runtime_event(event)

    def append_runtime_events(self, events: list[RuntimeEvent]) -> None:
        self.runner._append_runtime_events(events)


class ModuleDeliveryRuntime:
    """Applies authored module delivery requests to history, artifacts, and outbox."""

    def __init__(self, host: ModuleDeliveryHost):
        self.host = host

    def apply_request(
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

        artifact_store = ArtifactStore(self.host.home, self.host.session_id)
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
            self.host.append_runtime_event(
                RuntimeEvent(
                    type="artifact.stored",
                    aggregate_type="artifact",
                    aggregate_id=artifact.artifact_id,
                    payload={
                        "owner_turn_id": turn.turn_id,
                        "kind": artifact.kind,
                        "uri": artifact.path or artifact.url or "",
                        "metadata": {
                            "session_id": self.host.session_id,
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
            message = self.host.session_runtime.append_delivery_message(
                self.host.session_id,
                role="assistant",
                content=content,
                delivery_id=request.delivery_id,
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
            self.host.emit_event(
                "message.persisted",
                turn_id=turn.turn_id,
                message_id=message.id,
                role=message.role,
                kind=message.kind,
                **interaction_metadata,
            )
        else:
            self.host.append_runtime_events(
                [
                    RuntimeEvent(
                        type="delivery.queued",
                        aggregate_type="delivery",
                        aggregate_id=request.delivery_id,
                        payload={
                            "owner_turn_id": turn.turn_id,
                            "channel": interaction_metadata.get("channel"),
                            "target": delivery_target,
                            "status": "queued",
                            "idempotency_key": request.delivery_id,
                            "payload": delivery_payload,
                        },
                    ),
                    delivery_work_enqueued_event(
                        request.delivery_id,
                        owner_session_id=self.host.session_id,
                        owner_turn_id=turn.turn_id,
                        payload={
                            "owner_turn_id": turn.turn_id,
                            "channel": interaction_metadata.get("channel"),
                            "target": delivery_target,
                            "idempotency_key": request.delivery_id,
                            **delivery_payload,
                        },
                    ),
                ]
            )
        self.host.emit_event(
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
            self.host.emit_event(
                "delivery.degraded",
                turn_id=turn.turn_id,
                slot=slot.relative_path,
                delivery_id=request.delivery_id,
                reason="unsupported_blocks",
                count=unsupported_blocks,
                **interaction_metadata,
            )
        if interaction_metadata.get("channel") == "tui" and any(block.get("type") not in {"text"} for block in delivery_blocks):
            self.host.emit_event(
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
                data["resolved_path"] = str(
                    (self.host.home / "runtime" / "artifacts" / self.host.session_id / path).resolve()
                )
        return data
