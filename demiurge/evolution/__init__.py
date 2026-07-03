from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

from demiurge.core_repository import CommitResult, CoreRepository, PROTECTED_DEPENDENCY_FILES
from demiurge.gates import GateResult, GateRunner
from demiurge.util import write_json


@dataclass(slots=True)
class EvolverRunResult:
    summary: str
    session_id: str | None = None
    turn_id: str | None = None
    needs_user: bool = False


class EvolverRunner(Protocol):
    async def run(
        self,
        *,
        run_id: str,
        goal: str,
        target_core_id: str,
        agents_root: Path,
        target_core_path: Path,
        reference_agents_root: Path,
        run_root: Path,
    ) -> EvolverRunResult:
        ...


@dataclass(slots=True)
class EvolveResult:
    run_id: str
    target_core_id: str
    goal: str
    agents_root: str
    summary: str
    report_path: str
    changed_files: list[str] = field(default_factory=list)
    evolver: dict[str, Any] = field(default_factory=dict)
    proposal_revision: str | None = None
    promoted: bool = False
    new_revision: str | None = None
    gates: dict[str, Any] | None = None


@dataclass(slots=True)
class EvolveReview:
    run_id: str
    target_core_id: str
    changed_files: list[str]
    proposal_revision: str | None
    gates: GateResult
    report_path: str

    @property
    def passed(self) -> bool:
        return self.gates.passed


class EvolutionRuntime:
    def __init__(
        self,
        *,
        core_repository: CoreRepository,
        gate_runner: GateRunner,
        evolver_runner: EvolverRunner | None = None,
    ):
        self.core_repository = core_repository
        self.gate_runner = gate_runner
        self.evolver_runner = evolver_runner
        self._active_runs: set[str] = set()

    async def start(
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
        change_set = self.core_repository.begin_change_set(kind="evolve", reason=goal or f"evolve {target_core_id}")
        run_id = change_set.run_id
        try:
            request = {
                "run_id": run_id,
                "target_core_id": target_core_id,
                "goal": goal,
                "source_turn_id": source_turn_id,
                "base_revision": self.core_repository.live_revision(),
            }
            write_json(change_set.run_root / "request.json", request)
            evolver_result = await self.evolver_runner.run(
                run_id=run_id,
                goal=goal,
                target_core_id=target_core_id,
                agents_root=change_set.agents_root,
                target_core_path=change_set.agents_root / target_core_id,
                reference_agents_root=self.core_repository.active_agents_root(),
                run_root=change_set.run_root,
            )
            changed_files = change_set.changed_paths()
            evolver_payload = {
                "summary": evolver_result.summary,
                "session_id": evolver_result.session_id,
                "turn_id": evolver_result.turn_id,
                "needs_user": evolver_result.needs_user,
            }
            summary = (
                f"evolve run {run_id} ready for review"
                if changed_files
                else f"evolve run {run_id} made no changes"
            )
            payload = {
                "request": request,
                "changed_files": changed_files,
                "evolver": evolver_payload,
                "summary": summary,
            }
            write_json(change_set.run_root / "result.json", payload)
            change_set.write_report("Evolve Start Report", payload)
            return EvolveResult(
                run_id=run_id,
                target_core_id=target_core_id,
                goal=goal,
                agents_root=str(change_set.agents_root),
                summary=summary,
                report_path=str(change_set.report_path),
                changed_files=changed_files,
                evolver=evolver_payload,
            )
        finally:
            self._active_runs.discard(target_core_id)

    async def review(self, run_id: str, *, target_core_id: str = "assistant", goal: str = "") -> EvolveReview:
        change_set = self.core_repository.change_set(run_id)
        changed_files = change_set.changed_paths()
        commit: CommitResult | None = None
        if changed_files:
            commit = change_set.commit_proposal(
                reason=goal or f"review evolve run {run_id}",
                metadata={"target_core_id": target_core_id},
            )
        gates = await self.gate_runner.run(change_set.agents_root, changed_paths=changed_files)
        payload = {
            "run_id": run_id,
            "target_core_id": target_core_id,
            "changed_files": changed_files,
            "proposal_revision": commit.revision if commit else None,
            "gates": gates.as_dict(),
        }
        write_json(change_set.gates_path, gates.as_dict())
        write_json(change_set.proposal_path, payload)
        change_set.write_report("Evolve Review Report", payload)
        return EvolveReview(
            run_id=run_id,
            target_core_id=target_core_id,
            changed_files=changed_files,
            proposal_revision=commit.revision if commit else None,
            gates=gates,
            report_path=str(change_set.report_path),
        )

    async def promote(self, run_id: str, *, target_core_id: str = "assistant", reason: str = "evolve promote") -> EvolveResult:
        review = await self.review(run_id, target_core_id=target_core_id, goal=reason)
        if not review.passed:
            return EvolveResult(
                run_id=run_id,
                target_core_id=target_core_id,
                goal=reason,
                agents_root=str(self.core_repository.change_set(run_id).agents_root),
                summary=f"evolve run {run_id} failed gates",
                report_path=review.report_path,
                changed_files=review.changed_files,
                proposal_revision=review.proposal_revision,
                gates=review.gates.as_dict(),
                promoted=False,
            )
        if not review.proposal_revision:
            return EvolveResult(
                run_id=run_id,
                target_core_id=target_core_id,
                goal=reason,
                agents_root=str(self.core_repository.change_set(run_id).agents_root),
                summary=f"evolve run {run_id} has no proposal changes",
                report_path=review.report_path,
                changed_files=review.changed_files,
                proposal_revision=None,
                gates=review.gates.as_dict(),
                promoted=False,
            )
        result = self.core_repository.promote_run(run_id, reason=reason)
        return EvolveResult(
            run_id=run_id,
            target_core_id=target_core_id,
            goal=reason,
            agents_root=str(self.core_repository.active_agents_root()),
            summary=f"evolve promoted {run_id}@{result.revision[:12]}",
            report_path=review.report_path,
            changed_files=review.changed_files,
            proposal_revision=review.proposal_revision,
            gates=review.gates.as_dict(),
            promoted=True,
            new_revision=result.revision,
        )

    def discard(self, run_id: str) -> dict[str, Any]:
        change_set = self.core_repository.change_set(run_id)
        change_set.discard()
        return {"run_id": run_id, "discarded": True}

    async def evolve(
        self,
        *,
        target_core_id: str,
        goal: str,
        source_turn_id: str | None = None,
        auto_promote: bool = False,
    ) -> EvolveResult:
        result = await self.start(target_core_id=target_core_id, goal=goal, source_turn_id=source_turn_id)
        if auto_promote:
            return await self.promote(result.run_id, target_core_id=target_core_id, reason=goal)
        return result
