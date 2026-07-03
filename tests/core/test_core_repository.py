import pytest

from demiurge.app import source_agents_root
from demiurge.core_repository import CoreRepository, CoreRepositoryError, reject_dependency_files, reject_generated_artifacts


def test_core_repository_initializes_live_tree_and_refs(tmp_path):
    repo = CoreRepository(tmp_path / "home")

    pointer = repo.initialize_from_source(source_agents_root(), reason="test init")

    assert (repo.git_dir / "HEAD").exists()
    assert (repo.agents_root / "agent.yaml").exists()
    assert (repo.agents_root / "assistant" / "agent.yaml").exists()
    assert pointer.active_revision == repo.live_revision()
    assert pointer.previous_revision is None
    assert repo.previous_revision() is None
    assert repo.live_changed_paths() == []


def test_core_repository_change_set_proposal_promote_discard_and_rollback(tmp_path):
    repo = CoreRepository(tmp_path / "home")
    repo.initialize_from_source(source_agents_root(), reason="test init")
    original = repo.live_revision()

    change_set = repo.begin_change_set(kind="evolve", reason="edit soul", run_id="run_manual")
    soul = change_set.agents_root / "assistant" / "agent" / "SOUL.md"
    soul.write_text(soul.read_text(encoding="utf-8") + "\n\nRepository test edit.\n", encoding="utf-8")

    proposal = change_set.commit_proposal(reason="review")
    assert proposal.revision != original
    assert repo._run_git(["rev-parse", "--verify", repo.run_ref("run_manual")]).stdout.strip() == proposal.revision

    promoted = repo.promote_run("run_manual", reason="promote")
    assert promoted.revision == proposal.revision
    assert promoted.previous_revision == original
    assert repo.previous_revision() == original
    assert "Repository test edit." in (repo.agents_root / "assistant" / "agent" / "SOUL.md").read_text(encoding="utf-8")

    rollback = repo.rollback("previous", reason="rollback")
    assert rollback.previous_revision == promoted.revision
    assert repo.live_revision() == rollback.revision
    assert "Repository test edit." not in (repo.agents_root / "assistant" / "agent" / "SOUL.md").read_text(encoding="utf-8")

    disposable = repo.begin_change_set(kind="evolve", reason="discard", run_id="run_discard")
    run_root = disposable.run_root
    disposable.discard()
    assert not run_root.exists()


def test_core_repository_lock_rejects_concurrent_mutation(tmp_path):
    repo = CoreRepository(tmp_path / "home")
    repo.initialize_from_source(source_agents_root(), reason="test init")

    with repo.locked():
        with pytest.raises(CoreRepositoryError, match="locked"):
            with CoreRepository(tmp_path / "home").locked():
                pass


def test_core_repository_rejects_generated_artifacts_and_dependency_files(tmp_path):
    root = tmp_path / "agents"
    (root / "assistant" / "agent" / "__pycache__").mkdir(parents=True)
    (root / "assistant" / "agent" / "__pycache__" / "module.pyc").write_bytes(b"cache")
    (root / "assistant" / "pyproject.toml").write_text("[project]\nname='bad'\n", encoding="utf-8")

    assert reject_generated_artifacts(root) == ["assistant/agent/__pycache__/module.pyc"]
    assert reject_dependency_files(root) == ["assistant/pyproject.toml"]
