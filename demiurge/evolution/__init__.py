from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from demiurge.gates import GateResult, GateRunner
from demiurge.storage import VersionStore
from demiurge.util import append_jsonl, ensure_dir, require_relative_path, utc_id, write_json


@dataclass(slots=True)
class EvolveResult:
    run_id: str
    target_core_id: str
    goal: str
    candidate_path: str
    promoted: bool
    new_version: str | None
    summary: str
    gate_result: dict[str, Any]
    file_ops: list[dict[str, Any]] = field(default_factory=list)


class EvolutionRuntime:
    def __init__(self, *, version_store: VersionStore, gate_runner: GateRunner):
        self.version_store = version_store
        self.gate_runner = gate_runner
        self._active_runs: set[str] = set()

    async def evolve(
        self,
        *,
        target_core_id: str,
        goal: str,
        source_turn_id: str | None = None,
        file_ops: list[dict[str, Any]] | None = None,
    ) -> EvolveResult:
        if target_core_id in self._active_runs:
            raise RuntimeError(f"evolve already running for core: {target_core_id}")
        self._active_runs.add(target_core_id)
        run_id = utc_id("evolve_")
        candidate_path = self.version_store.create_candidate(target_core_id, run_id=run_id)
        run_root = candidate_path.parent
        try:
            ensure_dir(run_root / "logs")
            write_json(
                run_root / "request.json",
                {"run_id": run_id, "target_core_id": target_core_id, "goal": goal, "source_turn_id": source_turn_id},
            )
            ops = file_ops or self._default_file_ops(goal)
            applied_ops = self._apply_file_ops(candidate_path, ops)
            gate = await self.gate_runner.run(candidate_path)
            write_json(run_root / "result.json", {"gate": gate.as_dict(), "file_ops": applied_ops})
            if gate.passed:
                new_version = self.version_store.promote_candidate(
                    target_core_id,
                    candidate_path,
                    reason=f"evolve:{run_id}",
                )
                summary = f"evolve promoted {target_core_id}@{new_version}"
            else:
                new_version = None
                summary = f"evolve candidate failed gates for {target_core_id}"
            self._write_report(run_root / "report.md", goal=goal, gate=gate, new_version=new_version)
            result = EvolveResult(
                run_id=run_id,
                target_core_id=target_core_id,
                goal=goal,
                candidate_path=str(candidate_path),
                promoted=gate.passed,
                new_version=new_version,
                summary=summary,
                gate_result=gate.as_dict(),
                file_ops=applied_ops,
            )
            append_jsonl(
                self.version_store.history_root / target_core_id / "history.jsonl",
                {"type": "evolve", "run_id": run_id, "promoted": result.promoted, "new_version": new_version},
            )
            return result
        finally:
            self._active_runs.discard(target_core_id)

    def _default_file_ops(self, goal: str) -> list[dict[str, Any]]:
        note = goal.strip() or "unspecified functional change"
        return [
            {
                "op": "append",
                "path": "agent/SOUL.md",
                "content": f"\n\n## Evolution note\n- Goal: {note}\n",
            }
        ]

    def _apply_file_ops(self, candidate_path: Path, ops: list[dict[str, Any]]) -> list[dict[str, Any]]:
        applied: list[dict[str, Any]] = []
        for op in ops:
            action = op.get("op")
            rel_path = Path(str(op.get("path", "")))
            if rel_path.is_absolute() or ".." in rel_path.parts:
                raise ValueError(f"invalid candidate path: {rel_path}")
            if rel_path.parts and rel_path.parts[0] not in {"agent", "agent.yaml", "state", "tests", "evals"}:
                raise ValueError(f"path is outside editable authored surface: {rel_path}")
            if self._is_protected_io_path(rel_path) and not op.get("allow_protected_io"):
                raise ValueError(f"base IO module changes require explicit authorization: {rel_path}")
            if rel_path.name in {"pyproject.toml", "uv.lock", "requirements.txt", "requirements.in"}:
                raise ValueError(f"dependency file changes require manual review: {rel_path}")
            target = require_relative_path(candidate_path / rel_path, candidate_path)
            content = str(op.get("content", ""))
            ensure_dir(target.parent)
            if action == "write":
                target.write_text(content, encoding="utf-8")
            elif action == "append":
                existing = target.read_text(encoding="utf-8") if target.exists() else ""
                target.write_text(existing + content, encoding="utf-8")
            elif action == "json_merge":
                existing = json.loads(target.read_text(encoding="utf-8")) if target.exists() else {}
                patch = op.get("content", {})
                if not isinstance(existing, dict) or not isinstance(patch, dict):
                    raise ValueError("json_merge requires object content")
                existing.update(patch)
                target.write_text(json.dumps(existing, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            else:
                raise ValueError(f"unsupported file op: {action}")
            applied.append({"op": action, "path": rel_path.as_posix()})
        return applied

    def _is_protected_io_path(self, rel_path: Path) -> bool:
        parts = rel_path.parts
        protected_files = {
            ("agent", "input", "pipeline.yaml"),
            ("agent", "output", "pipeline.yaml"),
        }
        protected_modules = {
            ("agent", "input", "base_input"),
            ("agent", "output", "base_output"),
        }
        return parts in protected_files or (len(parts) >= 3 and parts[:3] in protected_modules)

    def _write_report(self, path: Path, *, goal: str, gate: GateResult, new_version: str | None) -> None:
        lines = [
            "# Evolve Report",
            "",
            f"- Goal: {goal}",
            f"- Promoted: {gate.passed}",
            f"- New version: {new_version or 'none'}",
            "",
            "## Gates",
            "",
        ]
        for phase in gate.phases:
            mark = "PASS" if phase.passed else "FAIL"
            lines.append(f"- {mark} {phase.name}: {phase.detail}")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
