from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from demiurge.core import CoreLoadError, CoreLoader
from demiurge.storage import VersionStore
from demiurge.util import append_jsonl, ensure_dir, utc_id, write_json


PROTECTED_DEPENDENCY_FILES = {"pyproject.toml", "uv.lock", "requirements.txt", "requirements.in"}


@dataclass(slots=True)
class EvolverRunResult:
    summary: str
    session_id: str | None = None
    turn_id: str | None = None


class EvolverRunner(Protocol):
    async def run(
        self,
        *,
        run_id: str,
        goal: str,
        target_core_id: str,
        candidate_path: Path,
        reference_core_path: Path,
        run_root: Path,
    ) -> EvolverRunResult:
        ...


@dataclass(slots=True)
class EvolveResult:
    run_id: str
    target_core_id: str
    goal: str
    candidate_path: str
    promoted: bool
    new_version: str | None
    summary: str
    report_path: str
    manifest_check: dict[str, Any]
    changed_files: list[str] = field(default_factory=list)
    evolver: dict[str, Any] = field(default_factory=dict)


class EvolutionRuntime:
    def __init__(
        self,
        *,
        version_store: VersionStore,
        core_loader: CoreLoader | None = None,
        evolver_runner: EvolverRunner | None = None,
    ):
        self.version_store = version_store
        self.core_loader = core_loader or CoreLoader()
        self.evolver_runner = evolver_runner
        self._active_runs: set[str] = set()

    async def evolve(
        self,
        *,
        target_core_id: str,
        goal: str,
        source_turn_id: str | None = None,
    ) -> EvolveResult:
        if target_core_id in self._active_runs:
            raise RuntimeError(f"evolve already running for core: {target_core_id}")
        if self.evolver_runner is None:
            raise RuntimeError("evolver runner is not configured")
        self._active_runs.add(target_core_id)
        run_id = utc_id("evolve_")
        reference_core_path = self.version_store.active_core_path(target_core_id)
        baseline = self._file_snapshot(reference_core_path)
        candidate_path = self.version_store.create_candidate(target_core_id, run_id=run_id)
        run_root = candidate_path.parent
        report_path = run_root / "report.md"
        try:
            ensure_dir(run_root / "logs")
            write_json(
                run_root / "request.json",
                {"run_id": run_id, "target_core_id": target_core_id, "goal": goal, "source_turn_id": source_turn_id},
            )
            evolver_result = await self.evolver_runner.run(
                run_id=run_id,
                goal=goal,
                target_core_id=target_core_id,
                candidate_path=candidate_path,
                reference_core_path=reference_core_path,
                run_root=run_root,
            )
            changed_files = self._changed_files(baseline, candidate_path)
            manifest_check = self._manifest_check(candidate_path)
            promoted = bool(changed_files) and bool(manifest_check["passed"])
            if promoted:
                new_version = self.version_store.promote_candidate(
                    target_core_id,
                    candidate_path,
                    reason=f"evolve:{run_id}",
                )
                summary = f"evolve promoted {target_core_id}@{new_version}"
            else:
                new_version = None
                if not changed_files:
                    summary = f"evolve made no candidate changes for {target_core_id}"
                else:
                    summary = f"evolve candidate failed manifest check for {target_core_id}"
            evolver_payload = {
                "summary": evolver_result.summary,
                "session_id": evolver_result.session_id,
                "turn_id": evolver_result.turn_id,
            }
            write_json(
                run_root / "result.json",
                {
                    "manifest_check": manifest_check,
                    "changed_files": changed_files,
                    "evolver": evolver_payload,
                    "promoted": promoted,
                    "new_version": new_version,
                },
            )
            self._write_report(
                report_path,
                goal=goal,
                manifest_check=manifest_check,
                changed_files=changed_files,
                evolver=evolver_payload,
                new_version=new_version,
            )
            result = EvolveResult(
                run_id=run_id,
                target_core_id=target_core_id,
                goal=goal,
                candidate_path=str(candidate_path),
                promoted=promoted,
                new_version=new_version,
                summary=summary,
                report_path=str(report_path),
                manifest_check=manifest_check,
                changed_files=changed_files,
                evolver=evolver_payload,
            )
            append_jsonl(
                self.version_store.history_root / target_core_id / "history.jsonl",
                {
                    "type": "evolve",
                    "run_id": run_id,
                    "promoted": result.promoted,
                    "new_version": new_version,
                    "changed_files": changed_files,
                    "manifest_check": manifest_check,
                },
            )
            return result
        finally:
            self._active_runs.discard(target_core_id)

    def _manifest_check(self, candidate_path: Path) -> dict[str, Any]:
        forbidden = sorted(
            path.relative_to(candidate_path).as_posix()
            for path in candidate_path.rglob("*")
            if path.is_file() and path.name in PROTECTED_DEPENDENCY_FILES
        )
        if forbidden:
            return {
                "passed": False,
                "detail": f"candidate declares dependency files: {forbidden}",
                "core_id": None,
                "version": None,
            }
        try:
            core = self.core_loader.load(candidate_path)
        except CoreLoadError as exc:
            return {"passed": False, "detail": str(exc), "core_id": None, "version": None}
        return {
            "passed": True,
            "detail": f"loaded {core.core_id}@{core.version}",
            "core_id": core.core_id,
            "version": core.version,
        }

    def _file_snapshot(self, root: Path) -> dict[str, str]:
        snapshot: dict[str, str] = {}
        for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
            if not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            snapshot[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        return snapshot

    def _changed_files(self, baseline: dict[str, str], candidate_path: Path) -> list[str]:
        current = self._file_snapshot(candidate_path)
        return sorted(
            path
            for path in set(baseline) | set(current)
            if baseline.get(path) != current.get(path)
        )

    def _write_report(
        self,
        path: Path,
        *,
        goal: str,
        manifest_check: dict[str, Any],
        changed_files: list[str],
        evolver: dict[str, Any],
        new_version: str | None,
    ) -> None:
        lines = [
            "# Evolve Report",
            "",
            f"- Goal: {goal}",
            f"- Promoted: {bool(new_version)}",
            f"- New version: {new_version or 'none'}",
            f"- Evolver session: {evolver.get('session_id') or 'none'}",
            f"- Evolver turn: {evolver.get('turn_id') or 'none'}",
            "",
            "## Manifest Check",
            "",
            f"- {'PASS' if manifest_check.get('passed') else 'FAIL'}: {manifest_check.get('detail')}",
            "",
            "## Changed Files",
            "",
        ]
        if changed_files:
            lines.extend(f"- {path}" for path in changed_files)
        else:
            lines.append("- none")
        lines.extend(["", "## Evolver Summary", "", evolver.get("summary") or "(none)"])
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
