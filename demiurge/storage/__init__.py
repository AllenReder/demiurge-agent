from __future__ import annotations

import copy
import hashlib
import json
import shutil
import threading
import time
import uuid
import weakref
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

import yaml

from demiurge.core_repository import CommitResult, CorePointer, CoreRepository
from demiurge.runtime.delivery import ArtifactRef
from demiurge.security.private_files import ensure_private_directory
from demiurge.util import (
    append_jsonl,
    atomic_write_private_json,
    atomic_write_private_text,
    read_json,
    utc_id,
    write_json,
)


class _StatePathLock:
    __slots__ = ("lock", "__weakref__")

    def __init__(self) -> None:
        self.lock = threading.RLock()


_STATE_PATH_LOCKS: weakref.WeakValueDictionary[str, _StatePathLock] = weakref.WeakValueDictionary()
_STATE_PATH_LOCKS_GUARD = threading.Lock()


def _state_path_lock(path: Path) -> _StatePathLock:
    resolved = str(path.resolve())
    with _STATE_PATH_LOCKS_GUARD:
        path_lock = _STATE_PATH_LOCKS.get(resolved)
        if path_lock is None:
            path_lock = _StatePathLock()
            _STATE_PATH_LOCKS[resolved] = path_lock
        return path_lock


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


@dataclass(slots=True)
class StateProposal:
    """Internal state mutation request validated and committed by StateStore."""

    target: str
    operation: str
    patch: Any


@dataclass(frozen=True, slots=True)
class StateSnapshot:
    document: dict[str, Any]
    revision: str


class StateConflictError(RuntimeError):
    def __init__(self, *, expected_revision: str, current_revision: str):
        self.expected_revision = expected_revision
        self.current_revision = current_revision
        super().__init__(
            "state revision conflict: "
            f"expected {expected_revision}, current {current_revision}"
        )


class StateCommitError(RuntimeError):
    pass


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
            artifact_dir = ensure_private_directory(self.root / artifact_id)
            filename = self._safe_filename(str(attachment.get("filename") or "payload.txt"))
            artifact_path = artifact_dir / filename
            atomic_write_private_text(artifact_path, str(content))
            path = artifact_path.relative_to(self.home / "runtime" / "artifacts" / self.session_id).as_posix()
        ensure_private_directory(self.root)
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
        self.transaction_journal = self.path.with_name(f".{self.path.name}.transaction.json")
        self._transaction_lock = _state_path_lock(self.path)
        self._proposal_log_lock = _state_path_lock(self.proposal_log)

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
        with self._transaction_lock.lock:
            if self.transaction_journal.exists():
                with self._proposal_log_lock.lock:
                    self._recover_transaction()
            return read_json(self.path, {"schema_version": 1})

    def snapshot(self) -> dict[str, Any]:
        return self.read()

    def read_snapshot(self) -> StateSnapshot:
        document = self.read()
        return StateSnapshot(document=document, revision=self._revision_for(document))

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
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        entry = {
            "id": utc_id("proposal_"),
            "transaction_id": uuid.uuid4().hex,
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
        with self._transaction_lock.lock, self._proposal_log_lock.lock:
            if accepted:
                snapshot = self.read_snapshot()
                entry["base_revision"] = snapshot.revision
                if expected_revision is not None and expected_revision != snapshot.revision:
                    entry["accepted"] = False
                    entry["reason"] = "state revision conflict"
                    entry["expected_revision"] = expected_revision
                    entry["state_revision"] = snapshot.revision
                    self._append_proposal_entry(entry)
                    raise StateConflictError(
                        expected_revision=expected_revision,
                        current_revision=snapshot.revision,
                    )
                previous_exists = self.path.exists()
                previous_document = snapshot.document
                state_before = (
                    self.path.read_text(encoding="utf-8")
                    if previous_exists
                    else None
                )
                proposal_log_exists = self.proposal_log.exists()
                document = copy.deepcopy(previous_document)
                self._apply(document, proposal)
                entry["state_revision"] = self._revision_for(document)
                state_after = self._document_text(document)
                journal = {
                    "schema_version": 1,
                    "phase": "prepared",
                    "proposal_id": entry["id"],
                    "proposal_entry": entry,
                    "state_existed": previous_exists,
                    "state_before": state_before,
                    "state_after": state_after,
                    "proposal_log_existed": proposal_log_exists,
                }
                atomic_write_private_json(self.transaction_journal, journal)
                try:
                    atomic_write_private_text(self.path, state_after)
                    self._append_proposal_entry(entry)
                    journal["phase"] = "committed"
                    atomic_write_private_json(self.transaction_journal, journal)
                except BaseException:
                    try:
                        self._recover_transaction()
                    except BaseException as recovery_error:
                        raise StateCommitError(
                            "state transaction failed and automatic recovery did not complete; "
                            f"journal retained at {self.transaction_journal}"
                        ) from recovery_error
                    raise
                else:
                    self.transaction_journal.unlink(missing_ok=True)
            else:
                self._append_proposal_entry(entry)
        return entry

    def _append_proposal_entry(
        self,
        entry: dict[str, Any],
        *,
        current_text: str | None = None,
    ) -> None:
        atomic_write_private_text(
            self.proposal_log,
            self._proposal_log_text(entry, current_text=current_text),
        )

    def _proposal_log_text(
        self,
        entry: dict[str, Any],
        *,
        current_text: str | None = None,
    ) -> str:
        if current_text is None:
            current_text = (
                self.proposal_log.read_text(encoding="utf-8")
                if self.proposal_log.exists()
                else ""
            )
        line = json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n"
        return current_text + line

    def _recover_transaction(self) -> None:
        journal = read_json(self.transaction_journal, None)
        if not isinstance(journal, dict):
            raise StateCommitError(f"invalid state transaction journal: {self.transaction_journal}")
        phase = journal.get("phase")
        if phase == "prepared":
            state_text = journal.get("state_before")
            state_existed = bool(journal.get("state_existed"))
        elif phase == "committed":
            state_text = journal.get("state_after")
            state_existed = True
        else:
            raise StateCommitError(
                f"unsupported state transaction phase {phase!r}: {self.transaction_journal}"
            )

        self._restore_file(self.path, state_text, existed=state_existed)
        self._recover_proposal_entry(
            journal.get("proposal_entry"),
            phase=phase,
            proposal_log_existed=bool(journal.get("proposal_log_existed")),
        )
        self.transaction_journal.unlink(missing_ok=True)

    def _recover_proposal_entry(
        self,
        proposal_entry: Any,
        *,
        phase: str,
        proposal_log_existed: bool,
    ) -> None:
        if not isinstance(proposal_entry, dict) or not isinstance(
            proposal_entry.get("transaction_id"),
            str,
        ):
            raise StateCommitError(
                f"state transaction proposal entry is invalid: {self.transaction_journal}"
            )
        transaction_id = proposal_entry["transaction_id"]
        entries = self._read_proposal_entries()
        matching_indexes = [
            index
            for index, entry in enumerate(entries)
            if entry.get("transaction_id") == transaction_id
        ]
        if phase == "prepared":
            if not matching_indexes:
                return
            entries = [
                entry
                for entry in entries
                if entry.get("transaction_id") != transaction_id
            ]
            if entries or proposal_log_existed:
                atomic_write_private_text(
                    self.proposal_log,
                    self._proposal_entries_text(entries),
                )
            else:
                self.proposal_log.unlink(missing_ok=True)
            return
        if not matching_indexes:
            self._append_proposal_entry(proposal_entry)

    def _read_proposal_entries(self) -> list[dict[str, Any]]:
        if not self.proposal_log.exists():
            return []
        entries: list[dict[str, Any]] = []
        for line in self.proposal_log.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            entry = json.loads(line)
            if not isinstance(entry, dict):
                raise StateCommitError(f"invalid proposal audit entry: {self.proposal_log}")
            entries.append(entry)
        return entries

    @staticmethod
    def _proposal_entries_text(entries: list[dict[str, Any]]) -> str:
        return "".join(
            json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n"
            for entry in entries
        )

    @staticmethod
    def _restore_file(path: Path, value: Any, *, existed: bool) -> None:
        if existed:
            if not isinstance(value, str):
                raise StateCommitError(f"state transaction recovery payload is invalid for {path}")
            current = path.read_text(encoding="utf-8") if path.exists() else None
            if current != value:
                atomic_write_private_text(path, value)
            return
        path.unlink(missing_ok=True)

    @staticmethod
    def _document_text(document: dict[str, Any]) -> str:
        return json.dumps(
            document,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ) + "\n"

    @staticmethod
    def _revision_for(document: dict[str, Any]) -> str:
        canonical = json.dumps(
            document,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(canonical).hexdigest()}"

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

    def __init__(
        self,
        home: Path,
        *,
        on_core_changed: Callable[[str], None] | None = None,
    ):
        self.home = home
        self.on_core_changed = on_core_changed
        self.notifies_core_changes = on_core_changed is not None
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
        if self.on_core_changed is not None:
            self.on_core_changed(core_id)
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
