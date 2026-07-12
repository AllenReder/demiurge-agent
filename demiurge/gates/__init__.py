from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from demiurge.core import CoreLoadError, CoreLoader, LoadedCore
from demiurge.core_repository import reject_dependency_files, reject_generated_artifacts
from demiurge.mcp.security import (
    mcp_connect_security_summary,
    mcp_server_fingerprint,
)


@dataclass(slots=True)
class GatePhase:
    name: str
    passed: bool
    detail: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class GateResult:
    passed: bool
    phases: list[GatePhase] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "manual_review_required": self.manual_review_required,
            "phases": [
                {
                    "name": phase.name,
                    "passed": phase.passed,
                    "detail": phase.detail,
                    **({"data": phase.data} if phase.data else {}),
                }
                for phase in self.phases
            ],
        }

    @property
    def manual_review_required(self) -> bool:
        return any(
            bool(phase.data.get("manual_review_required"))
            for phase in self.phases
        )

    @property
    def review_token(self) -> str | None:
        for phase in self.phases:
            if not phase.data.get("manual_review_required"):
                continue
            value = str(phase.data.get("review_token") or "").strip()
            if value:
                return value
        return None


class GateRunner:
    def __init__(self, *, project_root: Path):
        self.project_root = project_root
        self.loader = CoreLoader()

    async def run(
        self,
        agents_root: Path,
        *,
        changed_paths: list[str] | None = None,
        reference_agents_root: Path | None = None,
    ) -> GateResult:
        phases: list[GatePhase] = []
        agents_root = agents_root.expanduser().resolve()
        phases.append(self._path_gate(agents_root))
        phases.append(self._artifact_gate(agents_root))
        phases.append(self._dependency_gate(agents_root))
        phases.extend(self._load_core_gates(agents_root, changed_paths=changed_paths))
        phases.append(
            self.mcp_security_review(
                agents_root,
                changed_paths=changed_paths,
                reference_agents_root=reference_agents_root,
            )
        )
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

    def mcp_security_review(
        self,
        agents_root: Path,
        *,
        changed_paths: list[str] | None,
        reference_agents_root: Path | None,
    ) -> GatePhase:
        agents_root = agents_root.expanduser().resolve()
        try:
            mcp_paths = self._mcp_declaration_paths(
                agents_root,
                changed_paths=changed_paths,
                reference_agents_root=reference_agents_root,
            )
        except CoreLoadError as exc:
            return GatePhase("mcp_security", False, str(exc))
        if not mcp_paths:
            return GatePhase(
                "mcp_security",
                True,
                "no MCP declaration changes",
            )
        reference_root = (
            reference_agents_root.expanduser().resolve()
            if reference_agents_root is not None
            else None
        )
        candidate_cache: dict[str, dict[str, dict[str, Any]]] = {}
        reference_cache: dict[str, dict[str, dict[str, Any]]] = {}
        changes: list[dict[str, Any]] = []
        token_changes: list[dict[str, Any]] = []
        try:
            for changed_path in mcp_paths:
                parts = Path(changed_path).parts
                core_id = parts[0]
                relative_path = Path(*parts[1:]).as_posix()
                if core_id not in candidate_cache:
                    candidate_cache[core_id] = self._mcp_server_summaries(
                        agents_root,
                        core_id,
                    )
                if core_id not in reference_cache:
                    reference_cache[core_id] = (
                        self._mcp_server_summaries(reference_root, core_id)
                        if reference_root is not None
                        else {}
                    )
                before = reference_cache[core_id].get(relative_path)
                after = candidate_cache[core_id].get(relative_path)
                if before is None and after is not None:
                    change_kind = "added"
                elif before is not None and after is None:
                    change_kind = "removed"
                else:
                    change_kind = "modified"
                changes.append(
                    {
                        "path": changed_path,
                        "change": change_kind,
                        "before": (
                            before["summary"]
                            if before is not None
                            else None
                        ),
                        "after": (
                            after["summary"]
                            if after is not None
                            else None
                        ),
                    }
                )
                token_changes.append(
                    {
                        "path": changed_path,
                        "change": change_kind,
                        "before_fingerprint": (
                            before["fingerprint"]
                            if before is not None
                            else None
                        ),
                        "after_fingerprint": (
                            after["fingerprint"]
                            if after is not None
                            else None
                        ),
                    }
                )
        except CoreLoadError as exc:
            return GatePhase("mcp_security", False, str(exc))
        review_payload = {
            "changed_paths": mcp_paths,
            "changes": changes,
        }
        review_token = "mcp-review:" + hashlib.sha256(
            json.dumps(
                {
                    "changed_paths": mcp_paths,
                    "changes": token_changes,
                },
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return GatePhase(
            "mcp_security",
            True,
            (
                f"{len(mcp_paths)} MCP declaration change(s) require "
                "manual security review"
            ),
            data={
                "manual_review_required": True,
                **review_payload,
                "review_token": review_token,
            },
        )

    def _mcp_server_summaries(
        self,
        agents_root: Path,
        core_id: str,
    ) -> dict[str, dict[str, Any]]:
        core_path = agents_root / core_id
        if not core_path.exists():
            return {}
        core = self.loader.load(core_path)
        return {
            server.relative_path: {
                "summary": mcp_connect_security_summary(server),
                "fingerprint": mcp_server_fingerprint(server),
            }
            for server in core.mcp_servers
        }

    def _mcp_declaration_paths(
        self,
        agents_root: Path,
        *,
        changed_paths: list[str] | None,
        reference_agents_root: Path | None,
    ) -> list[str]:
        changed = {
            Path(path.rstrip("/")).as_posix()
            for path in changed_paths or []
        }
        reference_root = (
            reference_agents_root.expanduser().resolve()
            if reference_agents_root is not None
            else None
        )
        core_ids = {
            Path(path).parts[0]
            for path in changed
            if Path(path).parts
        }
        paths: set[str] = set()
        for core_id in sorted(core_ids):
            candidate = self._load_core_optional(agents_root, core_id)
            reference = (
                self._load_core_optional(reference_root, core_id)
                if reference_root is not None
                else None
            )
            candidate_paths = {
                f"{core_id}/{server.relative_path}"
                for server in candidate.mcp_servers
            } if candidate is not None else set()
            reference_paths = {
                f"{core_id}/{server.relative_path}"
                for server in reference.mcp_servers
            } if reference is not None else set()
            root_pointer_changed = (
                f"{core_id}/agent.yaml" in changed
                and self._configured_mcp_root(candidate)
                != self._configured_mcp_root(reference)
            )
            for declaration_path in candidate_paths | reference_paths:
                if root_pointer_changed or any(
                    self._paths_overlap(declaration_path, changed_path)
                    for changed_path in changed
                ):
                    paths.add(declaration_path)
        return sorted(paths)

    def _load_core_optional(
        self,
        agents_root: Path,
        core_id: str,
    ) -> LoadedCore | None:
        core_path = agents_root / core_id
        if not core_path.exists():
            return None
        return self.loader.load(core_path)

    @staticmethod
    def _configured_mcp_root(core: LoadedCore | None) -> str | None:
        if core is None:
            return None
        return core.manifest.slots.get("mcp") or (
            Path(core.manifest.runtime.surface_root) / "mcp"
        ).as_posix()

    @staticmethod
    def _paths_overlap(left: str, right: str) -> bool:
        left_parts = Path(left).parts
        right_parts = Path(right).parts
        common = min(len(left_parts), len(right_parts))
        return left_parts[:common] == right_parts[:common]

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
