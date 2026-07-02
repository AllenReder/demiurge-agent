from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping


JsonValue = Any
CONTENT_BLOCK_TYPES = {"text", "image", "audio", "video", "file", "control"}
DELIVERY_KINDS = {"message", "progress", "notice"}
DELIVERY_TARGETS = {"current"}
DELIVERY_MODES = {"immediate", "slot_end"}


@dataclass(slots=True)
class ArtifactRef:
    """Reference to an artifact already owned by the session artifact store."""

    artifact_id: str
    kind: str = "file"
    media_type: str | None = None
    path: str | None = None
    url: str | None = None
    summary: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ArtifactInput:
    """Author-facing artifact description before the host registers it."""

    kind: str = "file"
    media_type: str | None = None
    path: str | None = None
    url: str | None = None
    summary: str | None = None
    filename: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ContentBlock:
    """One ordered item in a delivery request.

    Modules describe content with blocks. The host decides how those blocks are
    persisted, converted to artifact references, and rendered by each channel.
    """

    type: str
    text: str | None = None
    artifact: ArtifactInput | ArtifactRef | Mapping[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DeliveryRequest:
    """Host-mediated output request produced by authored modules.

    A request is not a platform send result. The SDK records intent; the host
    later enforces capabilities, applies history policy, stores artifacts, and
    routes the resulting delivery to TUI, Telegram, or another channel.
    """

    delivery_id: str
    blocks: list[ContentBlock | Mapping[str, Any]] = field(default_factory=list)
    kind: str = "message"
    history_policy: str = "persist"
    delivery: str = "immediate"
    visible: bool = True
    target: str = "current"
    history_text: str | None = None
    failure_history_text: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DeliveryHandle:
    """Stable request id returned immediately to module code."""

    delivery_id: str


@dataclass(slots=True)
class DeliveryRouteContext:
    """Original inbound route captured so async output can return to the user."""

    session_id: str
    turn_id: str
    channel: str | None = None
    conversation_key: str | None = None
    source: str | None = None
    reply_to: str | None = None
    slot: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def artifact_input_to_dict(value: ArtifactInput | ArtifactRef | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(value, ArtifactInput):
        data = {
            "kind": value.kind,
            "media_type": value.media_type,
            "path": value.path,
            "url": value.url,
            "summary": value.summary,
            "filename": value.filename,
            "metadata": dict(value.metadata),
        }
    elif isinstance(value, ArtifactRef):
        data = {
            "artifact_id": value.artifact_id,
            "kind": value.kind,
            "media_type": value.media_type,
            "path": value.path,
            "url": value.url,
            "summary": value.summary,
            "metadata": dict(value.metadata),
        }
    else:
        data = dict(value)
    return {key: item for key, item in data.items() if item is not None}


def is_url(value: str) -> bool:
    lowered = value.lower()
    return lowered.startswith("http://") or lowered.startswith("https://")


def is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
