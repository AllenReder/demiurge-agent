from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class SessionRef:
    workspace_id: str
    session_id: str
    user_peer_id: str
    assistant_peer_id: str


def session_ref_from_ctx(ctx: Any, config: Mapping[str, Any]) -> SessionRef:
    workspace_path = _workspace_path(ctx)
    metadata = _metadata(ctx)
    session_id = _session_id(ctx)
    runtime_user = str(metadata.get("user_id") or metadata.get("user") or "").strip()
    peer_name = str(config.get("peer_name") or "").strip()
    user_peer = _sanitize(peer_name or runtime_user or f"user-{session_id}")
    assistant_peer = _sanitize(str(config.get("ai_peer") or "demiurge-assistant"))
    return SessionRef(
        workspace_id=_sanitize(str(config.get("workspace") or "demiurge")),
        session_id=_resolve_honcho_session_id(workspace_path, session_id, config),
        user_peer_id=user_peer,
        assistant_peer_id=assistant_peer,
    )


def _resolve_honcho_session_id(workspace_path: Path, session_id: str, config: Mapping[str, Any]) -> str:
    strategy = str(config.get("session_strategy") or "per-directory")
    if strategy == "per-session":
        return _sanitize(session_id)
    if strategy == "global":
        return _sanitize(str(config.get("workspace") or "demiurge"))
    if strategy in {"per-directory", "per-repo"}:
        return _sanitize(workspace_path.name or str(config.get("workspace") or "demiurge"))
    return _sanitize(session_id)


def _workspace_path(ctx: Any) -> Path:
    for candidate in (
        getattr(getattr(ctx, "input", None), "workspace", None),
        getattr(getattr(ctx, "output", None), "workspace", None),
        getattr(ctx, "workspace", None),
    ):
        if candidate:
            return Path(candidate)
    return Path.cwd()


def _metadata(ctx: Any) -> Mapping[str, Any]:
    turn = getattr(ctx, "turn", None)
    if turn is not None:
        metadata = getattr(turn, "metadata", None)
        if isinstance(metadata, Mapping):
            return metadata
    raw_input = getattr(getattr(ctx, "input", None), "raw_input", None)
    metadata = getattr(raw_input, "metadata", None)
    return metadata if isinstance(metadata, Mapping) else {}


def _session_id(ctx: Any) -> str:
    turn = getattr(ctx, "turn", None)
    session_id = getattr(turn, "session_id", None) if turn is not None else None
    if not session_id:
        session_id = getattr(ctx, "session_id", None)
    return str(session_id or "demiurge-session")


def _sanitize(value: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(value or "")).strip("-")
    text = text or "demiurge"
    if len(text) <= 100:
        return text
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
    return f"{text[:91].rstrip('-')}-{digest}"
