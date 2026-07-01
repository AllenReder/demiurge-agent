from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

from .session import SessionRef


class HonchoStore:
    def __init__(self, storage_dir: Path) -> None:
        self.storage_dir = storage_dir

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> "HonchoStore":
        return cls(Path(str(config.get("storage_dir") or "")).resolve())

    @property
    def cache_path(self) -> Path:
        return self.storage_dir / "cache.json"

    @property
    def outbox_path(self) -> Path:
        return self.storage_dir / "outbox.jsonl"

    @property
    def synced_path(self) -> Path:
        return self.storage_dir / "synced_turns.json"

    def read_cache(self, ref: SessionRef, *, max_age_seconds: int = 86400) -> dict[str, Any]:
        raw = self._read_json(self.cache_path, default={})
        key = _cache_key(ref)
        item = raw.get(key) if isinstance(raw, dict) else None
        if not isinstance(item, dict):
            return {}
        if (time.time() - float(item.get("updated_at") or 0)) > max_age_seconds:
            return {}
        context = item.get("context")
        return context if isinstance(context, dict) else {}

    def write_cache(self, ref: SessionRef, context: Mapping[str, Any]) -> None:
        if not context:
            return
        raw = self._read_json(self.cache_path, default={})
        data = raw if isinstance(raw, dict) else {}
        data[_cache_key(ref)] = {
            "updated_at": time.time(),
            "session": asdict(ref),
            "context": dict(context),
        }
        self._write_json(self.cache_path, data)

    def enqueue_turn(self, ref: SessionRef, *, turn_id: str, user_text: str, assistant_text: str) -> bool:
        if self.is_synced(turn_id):
            return False
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        record = {
            "turn_id": turn_id,
            "session": asdict(ref),
            "user_text": user_text,
            "assistant_text": assistant_text,
            "created_at": time.time(),
        }
        with self.outbox_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        return True

    def pending_turns(self, *, limit: int = 20) -> list[dict[str, Any]]:
        if not self.outbox_path.exists():
            return []
        records: list[dict[str, Any]] = []
        for line in self.outbox_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict) and not self.is_synced(str(item.get("turn_id") or "")):
                records.append(item)
            if len(records) >= limit:
                break
        return records

    def mark_synced(self, turn_id: str) -> None:
        if not turn_id:
            return
        data = self._read_json(self.synced_path, default={})
        synced = data if isinstance(data, dict) else {}
        synced[turn_id] = time.time()
        self._write_json(self.synced_path, synced)
        self._rewrite_outbox_excluding(set(synced))

    def is_synced(self, turn_id: str) -> bool:
        if not turn_id:
            return False
        data = self._read_json(self.synced_path, default={})
        return isinstance(data, dict) and turn_id in data

    def _rewrite_outbox_excluding(self, synced_turns: set[str]) -> None:
        if not self.outbox_path.exists():
            return
        remaining = [
            item
            for item in self.pending_turns(limit=10000)
            if str(item.get("turn_id") or "") not in synced_turns
        ]
        if not remaining:
            self.outbox_path.unlink(missing_ok=True)
            return
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        content = "\n".join(json.dumps(item, ensure_ascii=False) for item in remaining) + "\n"
        _write_text_atomic(self.outbox_path, content)

    def _read_json(self, path: Path, *, default: Any) -> Any:
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return default

    def _write_json(self, path: Path, data: Any) -> None:
        _write_text_atomic(path, json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))


def session_ref_from_record(record: Mapping[str, Any]) -> SessionRef | None:
    raw = record.get("session")
    if not isinstance(raw, Mapping):
        return None
    try:
        return SessionRef(
            workspace_id=str(raw["workspace_id"]),
            session_id=str(raw["session_id"]),
            user_peer_id=str(raw["user_peer_id"]),
            assistant_peer_id=str(raw["assistant_peer_id"]),
        )
    except KeyError:
        return None


def _cache_key(ref: SessionRef) -> str:
    return f"{ref.workspace_id}:{ref.session_id}:{ref.user_peer_id}:{ref.assistant_peer_id}"


def _write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), prefix=".honcho_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
