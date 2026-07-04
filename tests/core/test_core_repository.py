import shutil

import pytest
import yaml

from demiurge.app import source_agents_root
from demiurge.core_repository import LIVE_REF, PREVIOUS_REF, CoreRepository, CoreRepositoryError, reject_dependency_files, reject_generated_artifacts
from demiurge.gates import GatePhase, GateResult


def passing_gates() -> GateResult:
    return GateResult(True, [GatePhase("test", True, "passed")])


def failing_gates() -> GateResult:
    return GateResult(False, [GatePhase("test", False, "failed")])


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
    assert repo.core_ignore_path.exists()
    assert repo._run_git(["config", "--get", "core.excludesFile"]).stdout.strip() == str(repo.core_ignore_path)

    cache_dir = repo.agents_root / "assistant" / "agent" / "__pycache__"
    cache_dir.mkdir()
    (cache_dir / "module.cpython-311.pyc").write_bytes(b"cache")
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


def test_core_repository_rejects_stale_change_set_promotion(tmp_path):
    repo = CoreRepository(tmp_path / "home")
    repo.initialize_from_source(source_agents_root(), reason="test init")
    original = repo.live_revision()

    change_set = repo.begin_change_set(kind="evolve", reason="edit soul", run_id="run_stale")
    candidate_soul = change_set.agents_root / "assistant" / "agent" / "SOUL.md"
    candidate_soul.write_text(candidate_soul.read_text(encoding="utf-8") + "\n\nStale proposal edit.\n", encoding="utf-8")
    proposal = change_set.commit_proposal(reason="review")

    live_soul = repo.agents_root / "assistant" / "agent" / "SOUL.md"
    live_soul.write_text(live_soul.read_text(encoding="utf-8") + "\n\nNewer live edit.\n", encoding="utf-8")
    newer = repo.commit_live(reason="newer", summary="newer live edit")

    with pytest.raises(CoreRepositoryError, match="stale core proposal.*run_stale"):
        repo.promote_run("run_stale", reason="promote stale")

    assert repo.live_revision() == newer.revision
    assert repo.previous_revision() == original
    assert "Newer live edit." in live_soul.read_text(encoding="utf-8")
    assert "Stale proposal edit." not in live_soul.read_text(encoding="utf-8")
    assert proposal.revision != newer.revision


def test_core_repository_proposal_keeps_begin_base_revision(tmp_path):
    repo = CoreRepository(tmp_path / "home")
    repo.initialize_from_source(source_agents_root(), reason="test init")
    base = repo.live_revision()

    change_set = repo.begin_change_set(kind="evolve", reason="edit soul", run_id="run_base")
    live_soul = repo.agents_root / "assistant" / "agent" / "SOUL.md"
    live_soul.write_text(live_soul.read_text(encoding="utf-8") + "\n\nAdvance live while proposal is open.\n", encoding="utf-8")
    advanced = repo.commit_live(reason="advance", summary="advance live")

    candidate_soul = change_set.agents_root / "assistant" / "agent" / "SOUL.md"
    candidate_soul.write_text(candidate_soul.read_text(encoding="utf-8") + "\n\nProposal after live advanced.\n", encoding="utf-8")
    proposal = change_set.commit_proposal(reason="review")
    raw = yaml.safe_load(change_set.proposal_path.read_text(encoding="utf-8"))

    assert raw["base_revision"] == base
    assert proposal.previous_revision == base
    assert repo.live_revision() == advanced.revision


def test_core_repository_lock_rejects_concurrent_mutation(tmp_path):
    repo = CoreRepository(tmp_path / "home")
    repo.initialize_from_source(source_agents_root(), reason="test init")

    with repo.locked():
        with pytest.raises(CoreRepositoryError, match="locked"):
            with CoreRepository(tmp_path / "home").locked():
                pass


def test_core_repository_save_local_edits_noops_when_clean(tmp_path):
    repo = CoreRepository(tmp_path / "home")
    repo.initialize_from_source(source_agents_root(), reason="test init")

    result = repo.save_local_edits(validate=lambda _root, _paths: passing_gates())

    assert result.saved is False
    assert result.commit is None
    assert result.description.changed_paths == []


def test_core_repository_save_local_edits_cleans_ignored_python_cache(tmp_path):
    repo = CoreRepository(tmp_path / "home")
    repo.initialize_from_source(source_agents_root(), reason="test init")
    soul_path = repo.agents_root / "assistant" / "agent" / "SOUL.md"
    soul_path.write_text(soul_path.read_text(encoding="utf-8") + "\nmanual edit\n", encoding="utf-8")
    cache_dir = repo.agents_root / "assistant" / "agent" / "__pycache__"
    cache_dir.mkdir()
    (cache_dir / "module.cpython-311.pyc").write_bytes(b"cache")

    result = repo.save_local_edits(validate=lambda _root, _paths: passing_gates())

    assert result.saved is True
    assert not cache_dir.exists()
    assert result.description.changed_paths == ["assistant/agent/SOUL.md"]


def test_core_repository_saves_single_core_model_config_edit(tmp_path):
    repo = CoreRepository(tmp_path / "home")
    repo.initialize_from_source(source_agents_root(), reason="test init")
    original = repo.live_revision()
    manifest_path = repo.agents_root / "assistant" / "agent.yaml"
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    raw["model"]["provider"] = "deepseek"
    raw["model"]["model_name"] = "deepseek-v4-flash"
    manifest_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    result = repo.save_local_edits(validate=lambda _root, _paths: passing_gates())

    assert result.saved is True
    assert result.commit is not None
    assert result.commit.previous_revision == original
    assert result.description.summary == "update assistant model config"
    assert result.description.changed_scopes == ["assistant"]
    assert result.description.detected_changes == [
        "assistant/agent.yaml: model.model_name changed",
        "assistant/agent.yaml: model.provider changed",
    ]
    assert repo.live_changed_paths() == []
    message = repo._run_git(["log", "-1", "--format=%B", repo.live_revision()]).stdout
    assert "Source: external edit of runtime agents tree" in message
    assert "Gates: passed" in message


def test_core_repository_saves_global_fallback_edit(tmp_path):
    repo = CoreRepository(tmp_path / "home")
    repo.initialize_from_source(source_agents_root(), reason="test init")
    fallback_path = repo.agents_root / "agent.yaml"
    raw = yaml.safe_load(fallback_path.read_text(encoding="utf-8"))
    raw.setdefault("ui", {})["tool_display"] = "full"
    fallback_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    result = repo.save_local_edits(validate=lambda _root, _paths: passing_gates())

    assert result.saved is True
    assert result.description.summary == "update global fallback config"
    assert result.description.changed_scopes == ["global fallback"]
    assert "agent.yaml: ui.tool_display changed" in result.description.detected_changes


def test_core_repository_saves_multi_scope_local_edits_as_one_commit(tmp_path):
    repo = CoreRepository(tmp_path / "home")
    repo.initialize_from_source(source_agents_root(), reason="test init")
    fallback_path = repo.agents_root / "agent.yaml"
    raw = yaml.safe_load(fallback_path.read_text(encoding="utf-8"))
    raw.setdefault("ui", {})["tool_display"] = "full"
    fallback_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    soul_path = repo.agents_root / "assistant" / "agent" / "SOUL.md"
    soul_path.write_text(soul_path.read_text(encoding="utf-8") + "\nmanual edit\n", encoding="utf-8")

    result = repo.save_local_edits(validate=lambda _root, _paths: passing_gates())

    assert result.saved is True
    assert result.description.summary == "save local agent edits"
    assert result.description.changed_scopes == ["assistant", "global fallback"]
    assert repo._run_git(["rev-list", "--count", repo.live_revision()]).stdout.strip() == "2"


def test_core_repository_yaml_formatting_edit_has_no_detected_semantic_changes(tmp_path):
    repo = CoreRepository(tmp_path / "home")
    repo.initialize_from_source(source_agents_root(), reason="test init")
    manifest_path = repo.agents_root / "assistant" / "agent.yaml"
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    description = repo.describe_local_edits()

    assert description.changed_paths == ["assistant/agent.yaml"]
    assert description.detected_changes == []


def test_core_repository_save_local_edits_fails_closed_when_gates_fail(tmp_path):
    repo = CoreRepository(tmp_path / "home")
    repo.initialize_from_source(source_agents_root(), reason="test init")
    original = repo.live_revision()
    soul_path = repo.agents_root / "assistant" / "agent" / "SOUL.md"
    soul_path.write_text(soul_path.read_text(encoding="utf-8") + "\nmanual edit\n", encoding="utf-8")

    with pytest.raises(CoreRepositoryError, match="failed gates"):
        repo.save_local_edits(validate=lambda _root, _paths: failing_gates())

    assert repo.live_revision() == original
    assert repo.live_changed_paths() == ["assistant/agent/SOUL.md"]


def test_core_repository_live_transaction_failure_cleans_untracked_and_ignored_files(tmp_path):
    repo = CoreRepository(tmp_path / "home")
    repo.initialize_from_source(source_agents_root(), reason="test init")
    original = repo.live_revision()
    untracked = repo.agents_root / "assistant" / "agent" / "scratch.md"
    cache_dir = repo.agents_root / "assistant" / "agent" / "__pycache__"
    ignored = cache_dir / "module.cpython-311.pyc"

    with pytest.raises(RuntimeError, match="boom"):
        with repo.live_transaction(reason="test failure"):
            untracked.write_text("scratch\n", encoding="utf-8")
            cache_dir.mkdir()
            ignored.write_bytes(b"cache")
            raise RuntimeError("boom")

    assert repo.live_revision() == original
    assert repo.live_changed_paths() == []
    assert not untracked.exists()
    assert not ignored.exists()


def test_core_repository_consistency_report_ok_and_failures(tmp_path):
    repo = CoreRepository(tmp_path / "home")
    repo.initialize_from_source(source_agents_root(), reason="test init")

    ok = repo.check_consistency()
    assert ok.ok is True
    assert ok.issues == []

    repo._run_git(["update-ref", "-d", LIVE_REF])
    missing_live = repo.check_consistency()
    assert missing_live.ok is False
    assert any(issue.code == "core.live_ref.missing" for issue in missing_live.issues)

    repo._run_git(["update-ref", LIVE_REF, ok.live_revision])
    previous_ref_path = repo.git_dir / PREVIOUS_REF
    previous_ref_path.parent.mkdir(parents=True, exist_ok=True)
    previous_ref_path.write_text("not-a-revision\n", encoding="utf-8")
    bad_previous = repo.check_consistency()
    assert any(issue.code == "core.previous_ref.invalid" for issue in bad_previous.issues)

    repo._run_git(["update-ref", "-d", PREVIOUS_REF], check=False)
    soul = repo.agents_root / "assistant" / "agent" / "SOUL.md"
    soul.write_text(soul.read_text(encoding="utf-8") + "\n\nDetached mismatch.\n", encoding="utf-8")
    repo._run_git(["add", "-A"], work_tree=repo.agents_root)
    repo._run_git(["commit", "-m", "detached mismatch"], work_tree=repo.agents_root)
    mismatch = repo.check_consistency()
    assert any(issue.code == "core.checkout_head_mismatch" for issue in mismatch.issues)


def test_core_repository_status_reports_missing_checkout(tmp_path):
    repo = CoreRepository(tmp_path / "home")
    repo.initialize_from_source(source_agents_root(), reason="test init")
    shutil.rmtree(repo.agents_root)

    status = repo.status()

    assert status["dirty"] is False
    assert status["consistency"]["ok"] is False
    assert status["consistency"]["issues"][0]["code"] == "core.checkout_missing"


def test_core_repository_mutations_fail_closed_when_refs_are_inconsistent(tmp_path):
    repo = CoreRepository(tmp_path / "home")
    repo.initialize_from_source(source_agents_root(), reason="test init")
    live = repo.live_revision()
    repo._run_git(["update-ref", PREVIOUS_REF, live])
    soul = repo.agents_root / "assistant" / "agent" / "SOUL.md"
    soul.write_text(soul.read_text(encoding="utf-8") + "\n\nBlocked mutation.\n", encoding="utf-8")

    with pytest.raises(CoreRepositoryError, match="core.previous_ref_matches_live"):
        repo.commit_live(reason="blocked", summary="blocked")
    with pytest.raises(CoreRepositoryError, match="core.previous_ref_matches_live"):
        repo.save_local_edits()
    with pytest.raises(CoreRepositoryError, match="core.previous_ref_matches_live"):
        repo.begin_change_set(kind="evolve", reason="blocked", run_id="blocked_run")

    assert repo.live_revision() == live


def test_core_repository_discard_local_edits_resets_live_checkout(tmp_path):
    repo = CoreRepository(tmp_path / "home")
    repo.initialize_from_source(source_agents_root(), reason="test init")
    soul_path = repo.agents_root / "assistant" / "agent" / "SOUL.md"
    original_content = soul_path.read_text(encoding="utf-8")
    soul_path.write_text(original_content + "\nmanual edit\n", encoding="utf-8")
    untracked = repo.agents_root / "assistant" / "agent" / "scratch.md"
    untracked.write_text("scratch\n", encoding="utf-8")

    description = repo.discard_local_edits()

    assert description.changed_paths == ["assistant/agent/SOUL.md", "assistant/agent/scratch.md"]
    assert soul_path.read_text(encoding="utf-8") == original_content
    assert not untracked.exists()


def test_core_repository_rejects_generated_artifacts_and_dependency_files(tmp_path):
    root = tmp_path / "agents"
    (root / "assistant" / "agent" / "__pycache__").mkdir(parents=True)
    (root / "assistant" / "agent" / "__pycache__" / "module.pyc").write_bytes(b"cache")
    (root / "assistant" / "pyproject.toml").write_text("[project]\nname='bad'\n", encoding="utf-8")

    assert reject_generated_artifacts(root) == ["assistant/agent/__pycache__/module.pyc"]
    assert reject_dependency_files(root) == ["assistant/pyproject.toml"]
