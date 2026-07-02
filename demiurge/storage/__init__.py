from __future__ import annotations

import json
import shutil
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml

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
    core_version: str
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
    def __init__(self, home: Path, core_id: str):
        self.home = home
        self.core_id = core_id
        self.path = home / "state" / f"{core_id}.json"
        self.proposal_log = home / "state" / "proposals.jsonl"

    def read(self) -> dict[str, Any]:
        return read_json(self.path, {"schema_version": 1})

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
            "core_id": self.core_id,
            "turn_id": turn_id,
            "source": source,
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


@dataclass(slots=True)
class ActivePointer:
    core_id: str
    active_version: str
    previous_stable_version: str | None = None
    reason: str = "bootstrap"


class VersionStore:
    def __init__(self, home: Path):
        self.home = home
        self.agents_root = home / "agents"
        self.runs_root = home / "runs"
        self.history_root = home / "history"
        self.registry_root = home / "registry"

    @property
    def fallback_config_path(self) -> Path:
        return self.agents_root / "agent.yaml"

    def ensure_fallback_initialized(self, source_path: Path) -> None:
        if self.fallback_config_path.exists():
            return
        self.init_fallback_from_source(source_path, reason="auto init", overwrite=False)

    def init_fallback_from_source(self, source_path: Path, *, reason: str, overwrite: bool = True) -> str | None:
        source_path = source_path.resolve()
        if not source_path.exists():
            raise FileNotFoundError(f"source fallback agent config not found: {source_path}")
        if source_path.is_dir():
            raise IsADirectoryError(f"source fallback agent config is a directory: {source_path}")
        target = self.fallback_config_path
        previous: str | None = None
        if target.exists():
            if not overwrite:
                return None
            previous = self.backup_fallback(reason=reason)
            target.unlink()
        ensure_dir(target.parent)
        shutil.copy2(source_path, target)
        append_jsonl(
            self.history_root / "_global" / "history.jsonl",
            {
                "type": "fallback_init",
                "source": str(source_path),
                "target": str(target),
                "reason": reason,
            },
        )
        return previous

    def backup_fallback(self, *, reason: str) -> str | None:
        source = self.fallback_config_path
        if not source.exists():
            return None
        version = utc_id("fallback_")
        destination = self.history_root / "_global" / version / "agent.yaml"
        ensure_dir(destination.parent)
        shutil.copy2(source, destination)
        append_jsonl(
            self.history_root / "_global" / "history.jsonl",
            {
                "type": "fallback_backup",
                "version": version,
                "reason": reason,
            },
        )
        return version

    def ensure_initialized(self, core_id: str, source_core_path: Path) -> ActivePointer:
        if self.active_core_path(core_id).exists():
            return self.active_pointer(core_id)
        return self.init_from_source(core_id, source_core_path, reason="auto init")

    def init_from_source(self, core_id: str, source_core_path: Path, *, reason: str = "init") -> ActivePointer:
        source_core_path = source_core_path.resolve()
        if not source_core_path.exists():
            raise FileNotFoundError(f"source agent core not found: {source_core_path}")
        if not (source_core_path / "agent.yaml").exists():
            raise FileNotFoundError(f"source agent core missing agent.yaml: {source_core_path}")

        active_path = self.active_core_path(core_id)
        previous = self.backup_active(core_id, reason=reason) if active_path.exists() else None
        if active_path.exists():
            shutil.rmtree(active_path)
        ensure_dir(active_path.parent)
        shutil.copytree(source_core_path, active_path)
        active_version = self._manifest_version(active_path) or "0001"
        pointer = ActivePointer(
            core_id=core_id,
            active_version=active_version,
            previous_stable_version=previous,
            reason=reason,
        )
        self._write_pointer(pointer)
        append_jsonl(
            self._history_log(core_id),
            {
                "type": "init",
                "version": active_version,
                "previous": previous,
                "source": str(source_core_path),
                "reason": reason,
            },
        )
        return pointer

    def list_core_ids(self) -> list[str]:
        if not self.agents_root.exists():
            return []
        return sorted(path.name for path in self.agents_root.iterdir() if path.is_dir())

    def list_versions(self, core_id: str) -> list[str]:
        versions = set()
        history_root = self.history_root / core_id
        if history_root.exists():
            versions.update(path.name for path in history_root.iterdir() if path.is_dir())
        try:
            versions.add(self.active_pointer(core_id).active_version)
        except FileNotFoundError:
            pass
        return sorted(versions)

    def active_pointer(self, core_id: str) -> ActivePointer:
        data = read_json(self._pointer_path(core_id), None)
        if not data:
            active_path = self.active_core_path(core_id)
            if not active_path.exists():
                raise FileNotFoundError(f"no active core: {core_id}")
            data = asdict(
                ActivePointer(
                    core_id=core_id,
                    active_version=self._manifest_version(active_path) or "unknown",
                    reason="reconstructed",
                )
            )
            write_json(self._pointer_path(core_id), data)
        return ActivePointer(**data)

    def active_core_path(self, core_id: str) -> Path:
        return self.agents_root / core_id

    def version_path(self, core_id: str, version: str) -> Path:
        pointer = self.active_pointer(core_id)
        if version == pointer.active_version:
            return self.active_core_path(core_id)
        path = self.history_root / core_id / version
        if not path.exists():
            raise FileNotFoundError(f"core version not found: {core_id}@{version}")
        return path

    def create_candidate(self, core_id: str, run_id: str | None = None) -> Path:
        run_id = run_id or utc_id("evolve_")
        candidate = self.runs_root / core_id / run_id / "candidate"
        if candidate.exists():
            raise FileExistsError(f"candidate already exists: {candidate}")
        shutil.copytree(self.active_core_path(core_id), candidate)
        return candidate

    def promote_candidate(self, core_id: str, candidate_path: Path, *, reason: str) -> str:
        pointer = self.active_pointer(core_id)
        new_version = utc_id("v_")
        previous = self.backup_active(core_id, reason=reason, preferred_version=pointer.active_version)
        active_path = self.active_core_path(core_id)
        if active_path.exists():
            shutil.rmtree(active_path)
        shutil.copytree(candidate_path, active_path)
        self._rewrite_version(active_path / "agent.yaml", new_version, pointer.active_version)
        next_pointer = ActivePointer(
            core_id=core_id,
            active_version=new_version,
            previous_stable_version=previous,
            reason=reason,
        )
        self._write_pointer(next_pointer)
        append_jsonl(
            self._history_log(core_id),
            {
                "type": "promotion",
                "version": new_version,
                "previous": previous,
                "reason": reason,
            },
        )
        return new_version

    def rollback(self, core_id: str, target: str = "previous_stable", reason: str = "") -> ActivePointer:
        pointer = self.active_pointer(core_id)
        if target == "previous_stable":
            if not pointer.previous_stable_version:
                raise ValueError("no previous stable version recorded")
            target_version = pointer.previous_stable_version
        else:
            target_version = target
        if target_version == pointer.active_version:
            return pointer
        source = self.version_path(core_id, target_version)
        backup_version = self.backup_active(core_id, reason=reason or "rollback", preferred_version=pointer.active_version)
        active_path = self.active_core_path(core_id)
        if active_path.exists():
            shutil.rmtree(active_path)
        shutil.copytree(source, active_path)
        next_pointer = ActivePointer(
            core_id=core_id,
            active_version=target_version,
            previous_stable_version=backup_version,
            reason=reason or "rollback",
        )
        self._write_pointer(next_pointer)
        append_jsonl(
            self._history_log(core_id),
            {
                "type": "rollback",
                "version": target_version,
                "previous": backup_version,
                "reason": reason,
            },
        )
        return next_pointer

    def backup_active(
        self,
        core_id: str,
        *,
        reason: str,
        preferred_version: str | None = None,
    ) -> str | None:
        active_path = self.active_core_path(core_id)
        if not active_path.exists():
            return None
        version = preferred_version or self._manifest_version(active_path) or utc_id("v_")
        destination = self.history_root / core_id / version
        if destination.exists():
            version = utc_id(f"{version}-")
            destination = self.history_root / core_id / version
        ensure_dir(destination.parent)
        shutil.copytree(active_path, destination)
        append_jsonl(
            self._history_log(core_id),
            {
                "type": "backup",
                "version": version,
                "reason": reason,
            },
        )
        return version

    def _rewrite_version(self, manifest_path: Path, version: str, parent: str) -> None:
        raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        raw.setdefault("agent", {})
        raw["agent"]["version"] = version
        raw["agent"]["parent"] = parent
        manifest_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    def _manifest_version(self, core_path: Path) -> str | None:
        manifest_path = core_path / "agent.yaml"
        if not manifest_path.exists():
            return None
        raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        agent = raw.get("agent", {}) or {}
        value = agent.get("version")
        return str(value) if value is not None else None

    def _pointer_path(self, core_id: str) -> Path:
        return self.registry_root / f"{core_id}.json"

    def _write_pointer(self, pointer: ActivePointer) -> None:
        write_json(self._pointer_path(pointer.core_id), asdict(pointer))

    def _history_log(self, core_id: str) -> Path:
        return self.history_root / core_id / "history.jsonl"
