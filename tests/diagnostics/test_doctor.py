import yaml

from demiurge.app import init_runtime, source_agents_root
from demiurge.cli import main
from demiurge.diagnostics.doctor import DoctorRuntime


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

    main(["init", "--check", "--home", str(home), "--json"])

    output = capsys.readouterr().out
    assert '"findings"' in output
    assert not (home / "agents").exists()


def test_cli_init_refresh_backs_up_and_overwrites_runtime_core(tmp_path, capsys):
    home = tmp_path / "home"
    init_runtime(home=home, core_id="assistant", agents_root=source_agents_root())
    instructions = home / "agents" / "assistant" / "agent" / "SOUL.md"
    instructions.write_text("local edit", encoding="utf-8")

    main(["init", "--home", str(home), "--refresh", "assistant"])

    output = capsys.readouterr().out
    assert "refreshed assistant" in output
    assert "demiurge assistant" in instructions.read_text(encoding="utf-8")
    assert (home / "history" / "assistant" / "0001" / "agent" / "SOUL.md").read_text(
        encoding="utf-8"
    ) == "local edit"
