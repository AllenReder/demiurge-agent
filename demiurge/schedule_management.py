from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from demiurge.core import LoadedCore, ScheduleManifestInfo
from demiurge.util import ensure_dir, require_relative_path


class ScheduleManagementError(ValueError):
    pass


_SCHEDULE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_SLUG_RE = re.compile(r"[^A-Za-z0-9_-]+")


class ScheduleManager:
    def __init__(self, core: LoadedCore):
        self.core = core
        self.root = self._schedule_root()

    def list(self) -> dict[str, Any]:
        schedules = [self._payload(item) for item in self._read_all()]
        return {"success": True, "count": len(schedules), "schedules": schedules}

    def create(self, *, schedule_id: str | None, schedule: str, prompt: str) -> dict[str, Any]:
        schedule_id = self._normalize_create_id(schedule_id=schedule_id, prompt=prompt)
        path = self._new_schedule_path(schedule_id)
        manifest = self._validate_manifest({"schedule": schedule, "prompt": prompt})
        ensure_dir(path.parent)
        self._write_yaml(path, self._manifest_yaml(manifest))
        return {
            "success": True,
            "action": "create",
            "schedule": self._payload_from_manifest(schedule_id, path, manifest),
            "executionStarted": True,
        }

    def update(self, *, schedule_id: str, schedule: str | None = None, prompt: str | None = None) -> dict[str, Any]:
        if schedule is None and prompt is None:
            raise ScheduleManagementError("update requires schedule or prompt")
        path, raw = self._read_existing(schedule_id)
        if schedule is not None:
            raw["schedule"] = schedule
        if prompt is not None:
            raw["prompt"] = prompt
        manifest = self._validate_manifest(raw)
        self._write_yaml(path, raw)
        return {
            "success": True,
            "action": "update",
            "schedule": self._payload_from_manifest(schedule_id, path, manifest),
            "executionStarted": True,
        }

    def set_enabled(self, *, schedule_id: str, enabled: bool) -> dict[str, Any]:
        path, raw = self._read_existing(schedule_id)
        raw["enabled"] = enabled
        manifest = self._validate_manifest(raw)
        self._write_yaml(path, raw)
        return {
            "success": True,
            "action": "enable" if enabled else "disable",
            "schedule": self._payload_from_manifest(schedule_id, path, manifest),
            "executionStarted": True,
        }

    def delete(self, *, schedule_id: str) -> dict[str, Any]:
        path, raw = self._read_existing(schedule_id)
        manifest = self._validate_manifest(raw)
        path.unlink()
        return {
            "success": True,
            "action": "delete",
            "schedule": self._payload_from_manifest(schedule_id, path, manifest),
            "executionStarted": True,
        }

    def _schedule_root(self) -> Path:
        configured = self.core.manifest.slots.get("schedules")
        if configured is None:
            surface_root = require_relative_path(self.core.root / self.core.manifest.runtime.surface_root, self.core.root)
            configured = (surface_root / "schedules").relative_to(self.core.root).as_posix()
        return require_relative_path(self.core.root / configured, self.core.root)

    def _normalize_create_id(self, *, schedule_id: str | None, prompt: str) -> str:
        if schedule_id is not None and str(schedule_id).strip():
            return self._validate_schedule_id(str(schedule_id))
        words = _SLUG_RE.sub("-", prompt.strip().lower()).strip("-_")
        base = words[:48].strip("-_") or "schedule"
        if not base[0].isalnum():
            base = f"schedule-{base}"
        candidate = base
        index = 2
        while self._schedule_path_exists(candidate):
            suffix = f"-{index}"
            candidate = f"{base[:64 - len(suffix)]}{suffix}".strip("-_")
            index += 1
        return self._validate_schedule_id(candidate)

    def _validate_schedule_id(self, schedule_id: str) -> str:
        normalized = schedule_id.strip()
        if not _SCHEDULE_ID_RE.fullmatch(normalized):
            raise ScheduleManagementError(
                "schedule_id must start with a letter or digit and contain only letters, digits, underscores, or hyphens"
            )
        return normalized

    def _new_schedule_path(self, schedule_id: str) -> Path:
        if self._schedule_path_exists(schedule_id):
            raise ScheduleManagementError(f"schedule already exists: {schedule_id}")
        return require_relative_path(self.root / f"{schedule_id}.yaml", self.root)

    def _schedule_path_exists(self, schedule_id: str) -> bool:
        return (self.root / f"{schedule_id}.yaml").exists() or (self.root / f"{schedule_id}.yml").exists()

    def _read_existing(self, schedule_id: str) -> tuple[Path, dict[str, Any]]:
        schedule_id = self._validate_schedule_id(schedule_id)
        paths = [path for path in [self.root / f"{schedule_id}.yaml", self.root / f"{schedule_id}.yml"] if path.exists()]
        if not paths:
            raise ScheduleManagementError(f"schedule not found: {schedule_id}")
        if len(paths) > 1:
            raise ScheduleManagementError(f"duplicate schedule id: {schedule_id}")
        path = require_relative_path(paths[0], self.root)
        return path, self._read_raw(path)

    def _read_all(self) -> list[tuple[str, Path, ScheduleManifestInfo]]:
        if not self.root.exists():
            return []
        if not self.root.is_dir():
            raise ScheduleManagementError(f"schedule root is not a directory: {self.root}")
        found: list[tuple[str, Path, ScheduleManifestInfo]] = []
        seen: set[str] = set()
        for path in sorted(self.root.iterdir(), key=lambda item: item.name):
            if not path.is_file() or path.suffix.lower() not in {".yaml", ".yml"}:
                continue
            schedule_id = path.stem
            if schedule_id in seen:
                raise ScheduleManagementError(f"duplicate schedule id: {schedule_id}")
            seen.add(schedule_id)
            found.append((schedule_id, path, self._validate_manifest(self._read_raw(path))))
        return found

    def _read_raw(self, path: Path) -> dict[str, Any]:
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise ScheduleManagementError(f"invalid schedule yaml: {path.name}: {exc}") from exc
        if not isinstance(raw, dict):
            raise ScheduleManagementError(f"invalid schedule yaml: {path.name}: expected mapping")
        return dict(raw)

    def _validate_manifest(self, raw: dict[str, Any]) -> ScheduleManifestInfo:
        try:
            return ScheduleManifestInfo.model_validate(raw)
        except ValidationError as exc:
            raise ScheduleManagementError(f"invalid schedule: {exc}") from exc

    def _write_yaml(self, path: Path, raw: dict[str, Any]) -> None:
        target = require_relative_path(path, self.root)
        ensure_dir(target.parent)
        temp = target.with_name(f".{target.name}.tmp")
        temp.write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8")
        temp.replace(target)

    def _manifest_yaml(self, manifest: ScheduleManifestInfo) -> dict[str, Any]:
        return {
            "enabled": manifest.enabled,
            "schedule": manifest.schedule,
            "prompt": manifest.prompt,
            "modules": {
                "input": list(manifest.modules.input),
                "output": list(manifest.modules.output),
            },
            "delivery": {
                "mode": manifest.delivery.mode,
            },
        }

    def _payload(self, item: tuple[str, Path, ScheduleManifestInfo]) -> dict[str, Any]:
        schedule_id, path, manifest = item
        return self._payload_from_manifest(schedule_id, path, manifest)

    def _payload_from_manifest(self, schedule_id: str, path: Path, manifest: ScheduleManifestInfo) -> dict[str, Any]:
        prompt = manifest.prompt
        preview = prompt[:120] + "..." if len(prompt) > 120 else prompt
        return {
            "schedule_id": schedule_id,
            "enabled": manifest.enabled,
            "schedule": manifest.schedule,
            "prompt_preview": preview,
            "relative_path": path.relative_to(self.core.root).as_posix(),
        }
