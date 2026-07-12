import json
from pathlib import Path

import pytest
import yaml

from demiurge.app import source_agents_root
from demiurge.core_repository import CoreRepository, CoreRepositoryError
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


class CacheWritingRunner:
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
        cache_file = target_core_path / "agent" / "__pycache__" / "module.cpython-311.pyc"
        cache_file.parent.mkdir()
        cache_file.write_bytes(b"cache")
        runtime_file = agents_root / "runtime" / "tool-output.txt"
        runtime_file.parent.mkdir()
        runtime_file.write_text("runtime output\n", encoding="utf-8")
        return EvolverRunResult(summary=f"edited {target_core_id}", session_id="session_child", turn_id="turn_child")


class McpEditingRunner:
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
        manifest = target_core_path / "agent" / "mcp" / "docs.yaml"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(
            "transport: stdio\n"
            "command: npx\n"
            "args:\n"
            "  - --yes\n"
            "  - synthetic-package\n"
            "  - --token\n"
            "  - SYNTHETIC_MCP_SECRET\n"
            "cwd: .\n"
            "env:\n"
            "  MCP_TOKEN: SYNTHETIC_ENV_SECRET\n"
            "risk: high\n"
            "approval_policy: auto\n",
            encoding="utf-8",
        )
        return EvolverRunResult(
            summary=f"edited MCP for {target_core_id}",
            session_id="session_child",
            turn_id="turn_child",
        )


class McpRemovingRunner:
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
        (target_core_path / "agent" / "mcp" / "docs.yaml").unlink()
        return EvolverRunResult(summary=f"removed MCP for {target_core_id}")


class CustomRootMcpEditingRunner:
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
        manifest_path = target_core_path / "agent.yaml"
        raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        raw.setdefault("slots", {})["mcp"] = "connectors"
        manifest_path.write_text(
            yaml.safe_dump(raw, sort_keys=False),
            encoding="utf-8",
        )
        declaration = target_core_path / "connectors" / "docs.yaml"
        declaration.parent.mkdir(parents=True, exist_ok=True)
        declaration.write_text(
            "transport: stdio\n"
            "command: fake-custom-root\n"
            "approval_policy: prompt\n",
            encoding="utf-8",
        )
        return EvolverRunResult(summary=f"moved MCP for {target_core_id}")


@pytest.mark.asyncio
async def test_evolution_runtime_start_review_promote_and_discard(tmp_path):
    repo = CoreRepository(tmp_path / "home")
    repo.initialize_from_source(source_agents_root(), reason="test init")
    original = repo.live_revision()
    invalidated_cores = []
    runtime = EvolutionRuntime(
        core_repository=repo,
        gate_runner=GateRunner(project_root=tmp_path),
        evolver_runner=EditingRunner(),
        on_core_changed=invalidated_cores.append,
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
    assert invalidated_cores == ["assistant"]
    assert repo.previous_revision() == original
    assert "Evolved: make a test edit" in (repo.agents_root / "assistant" / "agent" / "SOUL.md").read_text(encoding="utf-8")

    discard = await runtime.start(target_core_id="assistant", goal="discard me", source_turn_id=None)
    run_root = repo.evolve_root / discard.run_id
    assert run_root.exists()
    assert runtime.discard(discard.run_id) == {"run_id": discard.run_id, "discarded": True}
    assert not run_root.exists()


@pytest.mark.asyncio
async def test_evolution_runtime_cleans_generated_artifacts_before_review(tmp_path):
    repo = CoreRepository(tmp_path / "home")
    repo.initialize_from_source(source_agents_root(), reason="test init")
    runtime = EvolutionRuntime(
        core_repository=repo,
        gate_runner=GateRunner(project_root=tmp_path),
        evolver_runner=CacheWritingRunner(),
    )

    started = await runtime.start(target_core_id="assistant", goal="make a test edit")

    candidate_root = Path(started.agents_root)
    assert started.changed_files == ["assistant/agent/SOUL.md"]
    assert not any(candidate_root.rglob("*.pyc"))
    assert not (candidate_root / "runtime" / "tool-output.txt").exists()

    review = await runtime.review(started.run_id, target_core_id="assistant")

    assert review.passed
    assert review.changed_files == ["assistant/agent/SOUL.md"]
    assert review.proposal_revision is not None
    tree = repo._run_git(["ls-tree", "-r", "--name-only", review.proposal_revision]).stdout
    assert "__pycache__" not in tree
    assert "runtime/tool-output.txt" not in tree


@pytest.mark.asyncio
async def test_evolution_review_preserves_change_set_base_revision(tmp_path):
    repo = CoreRepository(tmp_path / "home")
    repo.initialize_from_source(source_agents_root(), reason="test init")
    base = repo.live_revision()
    runtime = EvolutionRuntime(
        core_repository=repo,
        gate_runner=GateRunner(project_root=tmp_path),
        evolver_runner=EditingRunner(),
    )

    started = await runtime.start(target_core_id="assistant", goal="make a test edit")
    soul = repo.agents_root / "assistant" / "agent" / "SOUL.md"
    soul.write_text(soul.read_text(encoding="utf-8") + "\n\nLive advanced before review.\n", encoding="utf-8")
    advanced = repo.commit_live(reason="advance", summary="advance live")

    await runtime.review(started.run_id, target_core_id="assistant")

    proposal = json.loads((repo.evolve_root / started.run_id / "proposal.json").read_text(encoding="utf-8"))
    assert proposal["base_revision"] == base
    assert proposal["base_revision"] != advanced.revision


@pytest.mark.asyncio
async def test_evolution_start_saves_local_agent_edits_before_worktree(tmp_path):
    repo = CoreRepository(tmp_path / "home")
    repo.initialize_from_source(source_agents_root(), reason="test init")
    soul = repo.agents_root / "assistant" / "agent" / "SOUL.md"
    soul.write_text(soul.read_text(encoding="utf-8") + "\n\nManual evolve pre-edit.\n", encoding="utf-8")
    runtime = EvolutionRuntime(
        core_repository=repo,
        gate_runner=GateRunner(project_root=tmp_path),
        evolver_runner=EditingRunner(),
    )

    started = await runtime.start(target_core_id="assistant", goal="make a test edit")

    request = (repo.evolve_root / started.run_id / "request.json").read_text(encoding="utf-8")
    assert repo.live_changed_paths() == []
    assert repo.live_revision() in request
    assert "Manual evolve pre-edit." in (Path(started.agents_root) / "assistant" / "agent" / "SOUL.md").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_evolution_promote_rejects_dirty_live_tree(tmp_path):
    repo = CoreRepository(tmp_path / "home")
    repo.initialize_from_source(source_agents_root(), reason="test init")
    runtime = EvolutionRuntime(
        core_repository=repo,
        gate_runner=GateRunner(project_root=tmp_path),
        evolver_runner=EditingRunner(),
    )
    started = await runtime.start(target_core_id="assistant", goal="make a test edit")
    await runtime.review(started.run_id, target_core_id="assistant")
    soul = repo.agents_root / "assistant" / "agent" / "SOUL.md"
    soul.write_text(soul.read_text(encoding="utf-8") + "\n\nDirty before promote.\n", encoding="utf-8")

    with pytest.raises(CoreRepositoryError, match="local agent edits must be saved"):
        await runtime.promote(started.run_id, target_core_id="assistant")


@pytest.mark.asyncio
async def test_evolution_promote_rejects_stale_base_after_live_advances(tmp_path):
    repo = CoreRepository(tmp_path / "home")
    repo.initialize_from_source(source_agents_root(), reason="test init")
    original = repo.live_revision()
    runtime = EvolutionRuntime(
        core_repository=repo,
        gate_runner=GateRunner(project_root=tmp_path),
        evolver_runner=EditingRunner(),
    )
    started = await runtime.start(target_core_id="assistant", goal="make a test edit")
    soul = repo.agents_root / "assistant" / "agent" / "SOUL.md"
    soul.write_text(soul.read_text(encoding="utf-8") + "\n\nLive advanced before promote.\n", encoding="utf-8")
    advanced = repo.commit_live(reason="advance", summary="advance live")

    with pytest.raises(CoreRepositoryError, match=f"base={original}.*current={advanced.revision}"):
        await runtime.promote(started.run_id, target_core_id="assistant")

    assert repo.live_revision() == advanced.revision
    assert "Live advanced before promote." in soul.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_evolution_mcp_security_diff_requires_confirmed_manual_review(
    tmp_path,
):
    repo = CoreRepository(tmp_path / "home")
    repo.initialize_from_source(source_agents_root(), reason="test init")
    original = repo.live_revision()
    runtime = EvolutionRuntime(
        core_repository=repo,
        gate_runner=GateRunner(project_root=tmp_path),
        evolver_runner=McpEditingRunner(),
    )

    started = await runtime.start(
        target_core_id="assistant",
        goal="add reviewed MCP",
    )
    review = await runtime.review(
        started.run_id,
        target_core_id="assistant",
    )

    assert review.changed_files == ["assistant/agent/mcp/"]
    phase = next(
        phase for phase in review.gates.phases if phase.name == "mcp_security"
    )
    assert phase.passed is True
    assert phase.data["manual_review_required"] is True
    review_token = phase.data["review_token"]
    assert review_token.startswith("mcp-review:")
    assert phase.data["changed_paths"] == [
        "assistant/agent/mcp/docs.yaml"
    ]
    change = phase.data["changes"][0]
    assert change["change"] == "added"
    assert change["after"]["command"] == "npx"
    assert change["after"]["cwd"] == "."
    assert change["after"]["env_names"] == ["MCP_TOKEN"]
    assert change["after"]["risk"] == "high"
    assert change["after"]["approval_policy"] == "auto"
    assert change["after"]["connect_capability"] == "mcp.connect:docs"
    serialized = json.dumps(phase.data, sort_keys=True)
    assert "SYNTHETIC_MCP_SECRET" not in serialized
    assert "SYNTHETIC_ENV_SECRET" not in serialized

    blocked = await runtime.promote(
        started.run_id,
        target_core_id="assistant",
        reason="not yet confirmed",
    )

    assert blocked.promoted is False
    assert "manual MCP security review" in blocked.summary
    assert repo.live_revision() == original

    declaration = (
        repo.change_set(started.run_id).agents_root
        / "assistant/agent/mcp/docs.yaml"
    )
    declaration.write_text(
        declaration.read_text(encoding="utf-8").replace(
            "SYNTHETIC_ENV_SECRET",
            "SYNTHETIC_ENV_SECRET_ROTATED",
        ),
        encoding="utf-8",
    )
    stale = await runtime.promote(
        started.run_id,
        target_core_id="assistant",
        reason="stale MCP review token",
        manual_review_token=review_token,
    )

    assert stale.promoted is False
    assert repo.live_revision() == original

    refreshed = await runtime.review(
        started.run_id,
        target_core_id="assistant",
    )
    refreshed_phase = next(
        phase
        for phase in refreshed.gates.phases
        if phase.name == "mcp_security"
    )
    refreshed_token = refreshed_phase.data["review_token"]
    assert refreshed_token != review_token
    assert "SYNTHETIC_ENV_SECRET_ROTATED" not in json.dumps(
        refreshed_phase.data,
        sort_keys=True,
    )

    promoted = await runtime.promote(
        started.run_id,
        target_core_id="assistant",
        reason="confirmed MCP review",
        manual_review_token=refreshed_token,
    )

    assert promoted.promoted is True
    assert repo.live_revision() == promoted.new_revision


@pytest.mark.asyncio
async def test_evolution_mcp_security_diff_records_removed_declaration(
    tmp_path,
):
    repo = CoreRepository(tmp_path / "home")
    repo.initialize_from_source(source_agents_root(), reason="test init")
    manifest = repo.agents_root / "assistant" / "agent" / "mcp" / "docs.yaml"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        "transport: stdio\n"
        "command: fake-docs\n"
        "approval_policy: prompt\n",
        encoding="utf-8",
    )
    repo.commit_live(reason="add baseline MCP", summary="baseline MCP")
    runtime = EvolutionRuntime(
        core_repository=repo,
        gate_runner=GateRunner(project_root=tmp_path),
        evolver_runner=McpRemovingRunner(),
    )

    started = await runtime.start(
        target_core_id="assistant",
        goal="remove MCP",
    )
    review = await runtime.review(
        started.run_id,
        target_core_id="assistant",
    )

    phase = next(
        phase for phase in review.gates.phases if phase.name == "mcp_security"
    )
    change = phase.data["changes"][0]
    assert phase.data["manual_review_required"] is True
    assert change["path"] == "assistant/agent/mcp/docs.yaml"
    assert change["change"] == "removed"
    assert change["before"]["command"] == "fake-docs"
    assert change["after"] is None


@pytest.mark.asyncio
async def test_evolution_mcp_security_diff_follows_configured_root_pointer(
    tmp_path,
):
    repo = CoreRepository(tmp_path / "home")
    repo.initialize_from_source(source_agents_root(), reason="test init")
    runtime = EvolutionRuntime(
        core_repository=repo,
        gate_runner=GateRunner(project_root=tmp_path),
        evolver_runner=CustomRootMcpEditingRunner(),
    )

    started = await runtime.start(
        target_core_id="assistant",
        goal="move MCP root",
    )
    review = await runtime.review(
        started.run_id,
        target_core_id="assistant",
    )

    phase = next(
        phase for phase in review.gates.phases if phase.name == "mcp_security"
    )
    assert phase.data["manual_review_required"] is True
    assert phase.data["changed_paths"] == [
        "assistant/connectors/docs.yaml"
    ]
    change = phase.data["changes"][0]
    assert change["change"] == "added"
    assert change["after"]["path"] == "connectors/docs.yaml"
    assert change["after"]["command"] == "fake-custom-root"
