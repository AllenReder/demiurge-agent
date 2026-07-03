from pathlib import Path

import pytest

from demiurge.app import source_agents_root
from demiurge.core_repository import CoreRepository
from demiurge.evolution import EvolutionRuntime, EvolverRunResult
from demiurge.gates import GateRunner


class EditingRunner:
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
        soul = target_core_path / "agent" / "SOUL.md"
        soul.write_text(soul.read_text(encoding="utf-8") + f"\n\nEvolved: {goal}\n", encoding="utf-8")
        return EvolverRunResult(summary=f"edited {target_core_id}", session_id="session_child", turn_id="turn_child")


@pytest.mark.asyncio
async def test_evolution_runtime_start_review_promote_and_discard(tmp_path):
    repo = CoreRepository(tmp_path / "home")
    repo.initialize_from_source(source_agents_root(), reason="test init")
    original = repo.live_revision()
    runtime = EvolutionRuntime(
        core_repository=repo,
        gate_runner=GateRunner(project_root=tmp_path),
        evolver_runner=EditingRunner(),
    )

    started = await runtime.start(target_core_id="assistant", goal="make a test edit", source_turn_id="turn_parent")

    assert started.run_id
    assert started.promoted is False
    assert started.new_revision is None
    assert started.changed_files == ["assistant/agent/SOUL.md"]
    assert repo.live_revision() == original
    assert "Evolved: make a test edit" in (Path(started.agents_root) / "assistant" / "agent" / "SOUL.md").read_text(encoding="utf-8")

    review = await runtime.review(started.run_id, target_core_id="assistant")

    assert review.passed
    assert review.proposal_revision is not None
    assert review.changed_files == ["assistant/agent/SOUL.md"]
    assert repo.live_revision() == original

    promoted = await runtime.promote(started.run_id, target_core_id="assistant", reason="accept")

    assert promoted.promoted is True
    assert promoted.new_revision == review.proposal_revision
    assert repo.previous_revision() == original
    assert "Evolved: make a test edit" in (repo.agents_root / "assistant" / "agent" / "SOUL.md").read_text(encoding="utf-8")

    discard = await runtime.start(target_core_id="assistant", goal="discard me", source_turn_id=None)
    run_root = repo.evolve_root / discard.run_id
    assert run_root.exists()
    assert runtime.discard(discard.run_id) == {"run_id": discard.run_id, "discarded": True}
    assert not run_root.exists()
