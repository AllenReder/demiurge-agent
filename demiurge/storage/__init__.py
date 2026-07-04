from __future__ import annotations

import json
import shutil
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml

from demiurge.core_repository import CommitResult, CorePointer, CoreRepository
from demiurge.runtime.delivery import ArtifactRef
from demiurge.sdk import StateProposal
from demiurge.util import append_jsonl, ensure_dir, read_json, utc_id, write_json


class EventLog:
    def __init__(self, home: Path, session_id: str):
        self.home = home
        self.session_id = session_id
        self.path = home / "runtime" / "session-events" / f"{session_id}.jsonl"

    def emit(self, event_type: str, **data: Any) -> dict[str, Any]:
        event = {
            "id": utc_id("evt_"),
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "type": event_type,
            "session_id": self.session_id,
            **data,
        }
        append_jsonl(self.path, event)
        return event

    def read_all(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines]

    def tail(self, limit: int = 20, *, event_type: str | None = None) -> list[dict[str, Any]]:
        events = self.read_all()
        if event_type:
            events = [event for event in events if event.get("type") == event_type]
        return events[-limit:]

    def for_turn(self, turn_id: str) -> list[dict[str, Any]]:
        return [event for event in self.read_all() if event.get("turn_id") == turn_id]


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@dataclass(slots=True)
class SessionRecord:
    session_id: str
    core_id: str
    core_revision: str
    created_at: str
    updated_at: str
    channel: str | None = None
    conversation_key: str | None = None
    workspace: str | None = None
    provider: str | None = None
    model: str | None = None
    title: str | None = None
    preview: str | None = None
    message_count: int = 0
    compaction_summary_id: str | None = None
    compacted_until_message_id: str | None = None
    metadata: dict[str, Any] | None = None


@dataclass(slots=True)
class SessionMessage:
    id: str
    session_id: str
    turn_id: str | None
    role: str
    content: str
    created_at: str
    kind: str = "message"
    visible: bool = True
    model_visible: bool = True
    channel: str | None = None
    source: str | None = None
    reply_to: str | None = None
    conversation_key: str | None = None
    metadata: dict[str, Any] | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SessionMessage":
        return cls(
            id=str(data.get("id") or ""),
            session_id=str(data.get("session_id") or ""),
            turn_id=data.get("turn_id"),
            role=str(data.get("role") or "assistant"),
            content=str(data.get("content") or ""),
            created_at=str(data.get("created_at") or ""),
            kind=str(data.get("kind") or "message"),
            visible=bool(data.get("visible", True)),
            model_visible=bool(data.get("model_visible", True)),
            channel=data.get("channel"),
            source=data.get("source"),
            reply_to=data.get("reply_to"),
            conversation_key=data.get("conversation_key"),
            metadata=data.get("metadata") if isinstance(data.get("metadata"), dict) else None,
        )


@dataclass(slots=True)
class ArtifactRecord:
    artifact_id: str
    session_id: str
    kind: str
    created_at: str
    media_type: str | None = None
    path: str | None = None
    url: str | None = None
    summary: str | None = None
    metadata: dict[str, Any] | None = None


class ArtifactStore:
    def __init__(self, home: Path, session_id: str):
        self.home = home
        self.session_id = session_id
        self.root = home / "runtime" / "artifacts" / session_id

    def store(self, attachment: ArtifactRef | dict[str, Any]) -> ArtifactRef:
        if isinstance(attachment, ArtifactRef):
            return attachment
        artifact_id = str(attachment.get("artifact_id") or utc_id("artifact_"))
        kind = str(attachment.get("kind") or "file")
        media_type = attachment.get("media_type")
        url = attachment.get("url")
        summary = attachment.get("summary")
        metadata = attachment.get("metadata") if isinstance(attachment.get("metadata"), dict) else {}
        path = attachment.get("path")
        content = attachment.get("content")
        if content is not None:
            artifact_dir = ensure_dir(self.root / artifact_id)
            filename = self._safe_filename(str(attachment.get("filename") or "payload.txt"))
            artifact_path = artifact_dir / filename
            artifact_path.write_text(str(content), encoding="utf-8")
            path = artifact_path.relative_to(self.home / "runtime" / "artifacts" / self.session_id).as_posix()
        ensure_dir(self.root)
        record = ArtifactRecord(
            artifact_id=artifact_id,
            session_id=self.session_id,
            kind=kind,
            created_at=utc_now(),
            media_type=str(media_type) if media_type else None,
            path=str(path) if path else None,
            url=str(url) if url else None,
            summary=str(summary) if summary else None,
            metadata=metadata,
        )
        return ArtifactRef(
            artifact_id=artifact_id,
            kind=kind,
            media_type=record.media_type,
            path=record.path,
            url=record.url,
            summary=record.summary,
            metadata=metadata,
        )

    def _safe_filename(self, value: str) -> str:
        cleaned = "".join(ch if ch.isalnum() or ch in {".", "-", "_"} else "_" for ch in value.strip())
        return cleaned or "payload.txt"


class StateStore:
    def __init__(
        self,
        home: Path,
        core_id: str,
        *,
        scope: str = "core",
        session_id: str | None = None,
    ):
        if scope not in {"core", "session"}:
            raise ValueError(f"unsupported state scope: {scope}")
        if scope == "session" and not session_id:
            raise ValueError("session state requires session_id")
        self.home = home
        self.core_id = core_id
        self.scope = scope
        self.session_id = session_id
        self.path = self._path_for_scope(home, core_id, scope=scope, session_id=session_id)
        self.proposal_log = home / "state" / "proposals.jsonl"

    @classmethod
    def core(cls, home: Path, core_id: str) -> "StateStore":
        return cls(home, core_id, scope="core")

    @classmethod
    def session(cls, home: Path, *, core_id: str, session_id: str) -> "StateStore":
        return cls(home, core_id, scope="session", session_id=session_id)

    def _path_for_scope(self, home: Path, core_id: str, *, scope: str, session_id: str | None) -> Path:
        if scope == "core":
            return home / "state" / f"{core_id}.json"
        return home / "state" / "sessions" / f"{session_id}.json"

    def read(self) -> dict[str, Any]:
        return read_json(self.path, {"schema_version": 1})

    def snapshot(self) -> dict[str, Any]:
        return self.read()

    def read_target(self, target: str, default: Any = None) -> Any:
        cursor: Any = self.read()
        for part in [part for part in target.split(".") if part]:
            if not isinstance(cursor, dict) or part not in cursor:
                return default
            cursor = cursor[part]
        return cursor

    def submit(
        self,
        proposal: StateProposal,
        *,
        source: str,
        turn_id: str,
        accepted: bool = True,
        reason: str | None = None,
    ) -> dict[str, Any]:
        entry = {
            "id": utc_id("proposal_"),
            "scope": self.scope,
            "core_id": self.core_id,
            "session_id": self.session_id,
            "turn_id": turn_id,
            "source": source,
            "target": proposal.target,
            "operation": proposal.operation,
            "patch": proposal.patch,
            "proposal": {
                "target": proposal.target,
                "operation": proposal.operation,
                "patch": proposal.patch,
            },
            "accepted": accepted,
            "reason": reason,
        }
        if accepted:
            document = self.read()
            self._apply(document, proposal)
            write_json(self.path, document)
        append_jsonl(self.proposal_log, entry)
        return entry

    def _apply(self, document: dict[str, Any], proposal: StateProposal) -> None:
        if not proposal.target:
            raise ValueError("state proposal target is required")
        parts = [part for part in proposal.target.split(".") if part]
        cursor: dict[str, Any] = document
        for part in parts[:-1]:
            next_value = cursor.setdefault(part, {})
            if not isinstance(next_value, dict):
                raise ValueError(f"state target is not an object: {part}")
            cursor = next_value
        leaf = parts[-1]
        if proposal.operation == "set":
            cursor[leaf] = proposal.patch
        elif proposal.operation == "merge":
            current = cursor.setdefault(leaf, {})
            if not isinstance(current, dict) or not isinstance(proposal.patch, dict):
                raise ValueError("merge requires object target and object patch")
            current.update(proposal.patch)
        elif proposal.operation == "append":
            current = cursor.setdefault(leaf, [])
            if not isinstance(current, list):
                raise ValueError("append requires array target")
            current.append(proposal.patch)
        else:
            raise ValueError(f"unsupported state operation: {proposal.operation}")


ActivePointer = CorePointer


class VersionStore:
    """Compatibility facade for runtime core storage.

    Session, artifact, and state storage still live in this module. Core tree
    revision behavior is delegated to CoreRepository.
    """

    def __init__(self, home: Path):
        self.home = home
        self.core_repository = CoreRepository(home)
        self.agents_root = self.core_repository.agents_root
        self.runs_root = self.core_repository.evolve_root
        self.history_root = home / ".evolve"
        self.registry_root = self.core_repository.git_dir / "refs" / "demiurge"

    @property
    def fallback_config_path(self) -> Path:
        return self.core_repository.fallback_config_path

    def initialize_repository(self, source_agents_root: Path, *, reason: str = "init", force: bool = False) -> ActivePointer:
        return self.core_repository.initialize_from_source(source_agents_root, reason=reason, force=force)

    def refresh_repository(self, source_agents_root: Path, *, reason: str = "refresh") -> CommitResult:
        return self.core_repository.refresh_from_source(source_agents_root, reason=reason)

    def ensure_fallback_initialized(self, source_path: Path) -> None:
        source_agents = source_path.parent
        if self.core_repository.git_dir.exists():
            return
        self.core_repository.initialize_from_source(source_agents, reason="auto init", force=False)

    def init_fallback_from_source(self, source_path: Path, *, reason: str, overwrite: bool = True) -> str | None:
        source_agents = source_path.resolve().parent
        if not self.core_repository.git_dir.exists():
            pointer = self.core_repository.initialize_from_source(source_agents, reason=reason, force=False)
            return pointer.previous_revision
        if not overwrite:
            return None
        result = self.core_repository.refresh_from_source(source_agents, reason=reason)
        return result.previous_revision

    def backup_fallback(self, *, reason: str) -> str | None:
        return self.core_repository.live_revision() if self.core_repository.git_dir.exists() else None

    def ensure_initialized(self, core_id: str, source_core_path: Path) -> ActivePointer:
        if not self.core_repository.git_dir.exists():
            self.core_repository.initialize_from_source(source_core_path.parent, reason="auto init", force=False)
        return self.core_repository.ensure_core_from_source(core_id, source_core_path, reason="auto init")

    def init_from_source(self, core_id: str, source_core_path: Path, *, reason: str = "init") -> ActivePointer:
        source_core_path = source_core_path.resolve()
        if not self.core_repository.git_dir.exists():
            pointer = self.core_repository.initialize_from_source(source_core_path.parent, reason=reason, force=False)
            return ActivePointer(core_id=core_id, active_revision=pointer.active_revision, previous_revision=pointer.previous_revision, reason=reason)
        return self.core_repository.ensure_core_from_source(core_id, source_core_path, reason=reason)

    def list_core_ids(self) -> list[str]:
        if not self.agents_root.exists():
            return []
        return sorted(path.name for path in self.agents_root.iterdir() if path.is_dir())

    def list_versions(self, core_id: str) -> list[str]:
        return self.core_repository.list_revisions()

    def active_pointer(self, core_id: str) -> ActivePointer:
        if not self.active_core_path(core_id).exists():
            raise FileNotFoundError(f"no active core: {core_id}")
        return self.core_repository.active_pointer(core_id)

    def active_core_path(self, core_id: str) -> Path:
        return self.core_repository.active_core_path(core_id)

    def revision_path(self, core_id: str, revision: str) -> Path:
        pointer = self.active_pointer(core_id)
        if revision == pointer.active_revision:
            return self.active_core_path(core_id)
        raise FileNotFoundError(f"core revision is not checked out as a path: {core_id}@{revision}")

    def rollback(self, core_id: str, target: str = "previous", reason: str = "") -> ActivePointer:
        result = self.core_repository.rollback(target=target, reason=reason or "rollback")
        return ActivePointer(
            core_id=core_id,
            active_revision=result.revision,
            previous_revision=result.previous_revision,
            reason=reason or "rollback",
        )

    def _pointer_path(self, core_id: str) -> Path:
        return self.registry_root / core_id

    def _write_pointer(self, pointer: ActivePointer) -> None:
        return None

    def _history_log(self, core_id: str) -> Path:
        return self.history_root / core_id / "history.jsonl"
