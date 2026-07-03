from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from demiurge.core import CoreLoadError, CoreLoader
from demiurge.core_repository import reject_dependency_files, reject_generated_artifacts


@dataclass(slots=True)
class GatePhase:
    name: str
    passed: bool
    detail: str = ""


@dataclass(slots=True)
class GateResult:
    passed: bool
    phases: list[GatePhase] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "phases": [asdict(phase) for phase in self.phases],
        }


class GateRunner:
    def __init__(self, *, project_root: Path):
        self.project_root = project_root
        self.loader = CoreLoader()

    async def run(self, agents_root: Path, *, changed_paths: list[str] | None = None) -> GateResult:
        phases: list[GatePhase] = []
        agents_root = agents_root.expanduser().resolve()
        phases.append(self._path_gate(agents_root))
        phases.append(self._artifact_gate(agents_root))
        phases.append(self._dependency_gate(agents_root))
        phases.extend(self._load_core_gates(agents_root, changed_paths=changed_paths))
        phases.append(self._package_provenance_gate(agents_root))
        phases.append(self._cross_core_reference_gate(agents_root))
        return GateResult(all(phase.passed for phase in phases), phases)

    def _path_gate(self, agents_root: Path) -> GatePhase:
        if not agents_root.exists() or not agents_root.is_dir():
            return GatePhase("path_safety", False, f"agents tree is missing: {agents_root}")
        for path in agents_root.rglob("*"):
            if path.is_symlink():
                return GatePhase("path_safety", False, f"symlink is not allowed in agents tree: {path.relative_to(agents_root)}")
        return GatePhase("path_safety", True, "agents tree paths accepted")

    def _artifact_gate(self, agents_root: Path) -> GatePhase:
        rejected = reject_generated_artifacts(agents_root)
        if rejected:
            return GatePhase("artifact", False, f"generated/runtime artifacts are not allowed: {rejected[:20]}")
        return GatePhase("artifact", True, "no generated/runtime artifacts")

    def _dependency_gate(self, agents_root: Path) -> GatePhase:
        rejected = reject_dependency_files(agents_root)
        if rejected:
            return GatePhase("dependency", False, f"agent cores cannot declare host dependencies: {rejected}")
        return GatePhase("dependency", True, "host_shared dependencies only")

    def _load_core_gates(self, agents_root: Path, *, changed_paths: list[str] | None) -> list[GatePhase]:
        core_ids = self._changed_core_ids(agents_root, changed_paths)
        if not core_ids:
            core_ids = sorted(path.name for path in agents_root.iterdir() if path.is_dir() and (path / "agent.yaml").exists())
        phases: list[GatePhase] = []
        for core_id in core_ids:
            path = agents_root / core_id
            if not path.exists():
                phases.append(GatePhase(f"core_load:{core_id}", True, "core removed"))
                continue
            try:
                core = self.loader.load(path)
            except CoreLoadError as exc:
                phases.append(GatePhase(f"core_load:{core_id}", False, str(exc)))
                continue
            phases.append(GatePhase(f"core_load:{core_id}", True, f"loaded {core.core_id}"))
        return phases

    def _changed_core_ids(self, agents_root: Path, changed_paths: list[str] | None) -> list[str]:
        ids: set[str] = set()
        for rel in changed_paths or []:
            parts = Path(rel).parts
            if not parts:
                continue
            if parts[0] == "agent.yaml":
                continue
            if (agents_root / parts[0]).is_dir() or len(parts) > 1:
                ids.add(parts[0])
        return sorted(ids)

    def _package_provenance_gate(self, agents_root: Path) -> GatePhase:
        errors: list[str] = []
        for path in sorted(agents_root.glob("*/packages.yaml")):
            try:
                raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError as exc:
                errors.append(f"{path.relative_to(agents_root)}: {exc}")
                continue
            if raw.get("schema_version") != 1:
                errors.append(f"{path.relative_to(agents_root)}: schema_version must be 1")
                continue
            installed = raw.get("installed") or []
            if not isinstance(installed, list):
                errors.append(f"{path.relative_to(agents_root)}: installed must be a list")
                continue
            for index, item in enumerate(installed):
                if not isinstance(item, dict):
                    errors.append(f"{path.relative_to(agents_root)} installed[{index}] must be a mapping")
                    continue
                components = item.get("components") or []
                if not isinstance(components, list):
                    errors.append(f"{path.relative_to(agents_root)} installed[{index}].components must be a list")
                    continue
                for component_index, component in enumerate(components):
                    if not isinstance(component, dict):
                        errors.append(
                            f"{path.relative_to(agents_root)} installed[{index}].components[{component_index}] must be a mapping"
                        )
                        continue
                    if "installed_hash" not in component:
                        errors.append(
                            f"{path.relative_to(agents_root)} installed[{index}].components[{component_index}] missing installed_hash"
                        )
        if errors:
            return GatePhase("package_provenance", False, "; ".join(errors[:10]))
        return GatePhase("package_provenance", True, "package provenance accepted")

    def _cross_core_reference_gate(self, agents_root: Path) -> GatePhase:
        core_ids = {path.name for path in agents_root.iterdir() if path.is_dir() and (path / "agent.yaml").exists()}
        errors: list[str] = []
        for path in sorted(agents_root.glob("*/packages.yaml")):
            core_id = path.parent.name
            try:
                raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError:
                continue
            for item in raw.get("installed") or []:
                if not isinstance(item, dict):
                    continue
                for component in item.get("components") or []:
                    if not isinstance(component, dict) or component.get("kind") != "core":
                        continue
                    target_core_id = str(component.get("target_core_id") or "").strip()
                    if target_core_id and target_core_id not in core_ids:
                        errors.append(f"{core_id}/packages.yaml references missing core {target_core_id}")
        if errors:
            return GatePhase("cross_core_references", False, "; ".join(errors[:10]))
        return GatePhase("cross_core_references", True, "cross-core references accepted")


def run_gate_sync(runner: GateRunner, agents_root: Path) -> GateResult:
    return asyncio.run(runner.run(agents_root))
