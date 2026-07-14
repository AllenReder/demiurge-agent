import shutil
import os
import stat

import pytest
import yaml

from demiurge.app import init_runtime, source_agents_root
from demiurge.cli import main
from demiurge.core_repository import LIVE_REF
from demiurge.diagnostics.doctor import DoctorRuntime
from demiurge.storage import VersionStore


def test_doctor_detects_runtime_missing_source_tool(tmp_path):
    home = tmp_path / "home"
    init_runtime(home=home, core_id="assistant", agents_root=source_agents_root())
    manifest_path = home / "agents" / "assistant" / "agent.yaml"
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    raw["tools"]["toolsets"].remove("demiurge_control")
    manifest_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    report = DoctorRuntime(home=home, source_agents_root=source_agents_root(), core_id="assistant").run()

    finding = next(item for item in report.findings if item.code == "core.tools.missing")
    assert finding.severity == "warning"
    assert "tools_list" in finding.details["missing"]
    assert "init --refresh assistant" in finding.remediation


def test_doctor_reports_missing_runtime_without_initializing(tmp_path):
    home = tmp_path / "missing-home"

    report = DoctorRuntime(home=home, source_agents_root=source_agents_root(), core_id="assistant").run()

    codes = {item.code for item in report.findings}
    assert "runtime.fallback.missing" in codes
    assert "runtime.core.missing" in codes
    assert not (home / "agents").exists()


def test_doctor_reports_invalid_global_fallback(tmp_path):
    home = tmp_path / "home"
    init_runtime(home=home, core_id="assistant", agents_root=source_agents_root())
    (home / "agents" / "agent.yaml").write_text("model: {}\nslots: {}\n", encoding="utf-8")

    report = DoctorRuntime(home=home, source_agents_root=source_agents_root(), core_id="assistant").run()

    finding = next(item for item in report.findings if item.code == "runtime.fallback.invalid")
    assert finding.severity == "error"
    assert "model, ui, and approval" in finding.remediation


def test_doctor_ignores_runtime_core_channel_slot_declaration(tmp_path):
    home = tmp_path / "home"
    init_runtime(home=home, core_id="assistant", agents_root=source_agents_root())
    manifest_path = home / "agents" / "assistant" / "agent.yaml"
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    raw.setdefault("slots", {})["channels"] = "agent/channels"
    manifest_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    report = DoctorRuntime(home=home, source_agents_root=source_agents_root(), core_id="assistant").run()

    codes = {item.code for item in report.findings}
    assert "runtime.core.invalid" not in codes


def test_cli_init_check_is_read_only(tmp_path, capsys):
    home = tmp_path / "home"

    with pytest.raises(SystemExit, match="1"):
        main(["init", "--check", "--home", str(home), "--json"])

    output = capsys.readouterr().out
    assert '"findings"' in output
    assert not (home / "agents").exists()


def test_doctor_reports_core_repository_consistency_errors(tmp_path):
    home = tmp_path / "home"
    init_runtime(home=home, core_id="assistant", agents_root=source_agents_root())
    store = VersionStore(home)
    store.core_repository._run_git(["update-ref", "-d", LIVE_REF])

    report = DoctorRuntime(home=home, source_agents_root=source_agents_root(), core_id="assistant").run()

    finding = next(item for item in report.findings if item.code == "core.repository.inconsistent")
    assert finding.severity == "error"
    assert "core.live_ref.missing" in finding.details["issues"]
    assert "demiurge core status" in finding.remediation


@pytest.mark.skipif(os.name == "nt", reason="Windows uses platform ACL semantics")
def test_doctor_reports_insecure_runtime_permissions_without_changing_them(tmp_path):
    home = tmp_path / "home"
    init_runtime(home=home, core_id="assistant", agents_root=source_agents_root())
    config_path = home / "config.yaml"
    os.chmod(config_path, 0o644)

    report = DoctorRuntime(
        home=home,
        source_agents_root=source_agents_root(),
        core_id="assistant",
    ).run()

    finding = next(
        item
        for item in report.findings
        if item.code == "runtime.permissions.insecure"
    )
    assert finding.severity == "error"
    assert str(config_path) in finding.details["paths"]
    assert stat.S_IMODE(config_path.stat().st_mode) == 0o644


def test_cli_init_refresh_refuses_dirty_runtime_core(tmp_path, capsys):
    home = tmp_path / "home"
    init_runtime(home=home, core_id="assistant", agents_root=source_agents_root())
    instructions = home / "agents" / "assistant" / "agent" / "SOUL.md"
    instructions.write_text("local edit", encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        main(["init", "--home", str(home), "--refresh", "assistant"])

    assert "uncommitted changes" in str(exc.value)
    assert instructions.read_text(encoding="utf-8") == "local edit"
    assert not capsys.readouterr().out


def test_cli_init_refresh_commits_source_agents_tree(tmp_path, capsys):
    source = tmp_path / "source-agents"
    shutil.copytree(source_agents_root(), source)
    source_instructions = source / "assistant" / "agent" / "SOUL.md"
    source_instructions.write_text("source refresh content", encoding="utf-8")
    home = tmp_path / "home"
    init_runtime(home=home, core_id="assistant", agents_root=source_agents_root())
    version_store = VersionStore(home)
    before = version_store.core_repository.live_revision()

    main(["init", "--home", str(home), "--agents-root", str(source), "--refresh", "assistant"])

    after = version_store.core_repository.live_revision()
    instructions = home / "agents" / "assistant" / "agent" / "SOUL.md"
    output = capsys.readouterr().out
    assert "refreshed assistant" in output
    assert after != before
    assert version_store.core_repository.previous_revision() == before
    assert instructions.read_text(encoding="utf-8") == "source refresh content"
    assert not (home / "history").exists()
