import json
import subprocess
import sys
from io import StringIO

import pytest
import yaml
from rich.console import Console

from demiurge import cli
from demiurge import setup_cli
from demiurge.app import init_runtime, source_agents_root
from demiurge.provider_presets import get_provider_preset
from demiurge.providers import LLMResponse
from demiurge.storage import VersionStore
from demiurge.ui import tui_launcher


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "demiurge", *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


def _filesystem_snapshot(root):
    if not root.exists():
        return {}
    snapshot = {}
    for path in sorted(root.rglob("*")):
        relative = str(path.relative_to(root))
        if path.is_symlink():
            snapshot[relative] = ("symlink", str(path.readlink()))
        elif path.is_dir():
            snapshot[relative] = ("dir",)
        else:
            snapshot[relative] = ("file", path.read_bytes())
    return snapshot


def _assert_filesystem_snapshot(root, before):
    after = _filesystem_snapshot(root)
    changed = sorted(path for path in before.keys() | after.keys() if before.get(path) != after.get(path))
    assert not changed, f"filesystem changed ({len(changed)} paths): {changed[:20]}"


def test_cli_01_doctor_json_error_exits_nonzero(tmp_path):
    """CLI-01: doctor JSON ok:false must be a failing process result."""
    completed = _run_cli(
        "--home",
        str(tmp_path / "home"),
        "--agents-root",
        str(tmp_path / "missing-agents"),
        "doctor",
        "--json",
    )

    assert json.loads(completed.stdout)["ok"] is False
    assert completed.returncode != 0


def test_cli_01_init_check_json_error_exits_nonzero(tmp_path):
    """CLI-01: init --check JSON ok:false must be a failing process result."""
    completed = _run_cli(
        "--agents-root",
        str(tmp_path / "missing-agents"),
        "init",
        "--home",
        str(tmp_path / "home"),
        "--check",
        "--json",
    )

    assert json.loads(completed.stdout)["ok"] is False
    assert completed.returncode != 0


def test_cli_02_init_check_preserves_runtime_filesystem_snapshot(tmp_path):
    """CLI-02: init --check is read-only, including a custom .core-ignore."""
    home = tmp_path / "home"
    init_runtime(home=home, agents_root=source_agents_root())
    core_ignore = home / ".core-ignore"
    core_ignore.write_text("sentinel\n", encoding="utf-8")
    before = _filesystem_snapshot(home)

    completed = _run_cli(
        "--agents-root",
        str(source_agents_root()),
        "init",
        "--home",
        str(home),
        "--check",
        "--json",
    )

    assert completed.returncode == 0
    assert json.loads(completed.stdout)["ok"] is True
    _assert_filesystem_snapshot(home, before)


def test_cli_02_setup_status_preserves_missing_home_filesystem_snapshot(tmp_path):
    """CLI-02: setup status probes a missing home without initializing it."""
    home = tmp_path / "missing-home"
    before = _filesystem_snapshot(tmp_path)

    completed = _run_cli(
        "--home",
        str(home),
        "setup",
        "status",
        "--json",
    )

    assert completed.returncode == 0
    assert json.loads(completed.stdout)["home"] == str(home)
    _assert_filesystem_snapshot(tmp_path, before)


def test_cli_02_doctor_preserves_missing_home_filesystem_snapshot(tmp_path):
    """CLI-02: doctor probes a missing home without initializing it."""
    home = tmp_path / "missing-home"
    before = _filesystem_snapshot(tmp_path)

    completed = _run_cli(
        "--home",
        str(home),
        "--agents-root",
        str(tmp_path / "missing-agents"),
        "doctor",
        "--json",
    )

    assert json.loads(completed.stdout)["ok"] is False
    _assert_filesystem_snapshot(tmp_path, before)


def test_cli_02_doctor_preserves_initialized_home_filesystem_snapshot(tmp_path):
    """CLI-02: doctor does not rewrite an initialized runtime home."""
    home = tmp_path / "home"
    init_runtime(home=home, agents_root=source_agents_root())
    core_ignore = home / ".core-ignore"
    core_ignore.write_text("sentinel\n", encoding="utf-8")
    before = _filesystem_snapshot(home)

    completed = _run_cli(
        "--home",
        str(home),
        "--agents-root",
        str(source_agents_root()),
        "--provider",
        "fake",
        "doctor",
        "--json",
    )

    assert "ok" in json.loads(completed.stdout)
    _assert_filesystem_snapshot(home, before)


def test_default_cli_launches_ts_tui(monkeypatch, tmp_path):
    called = {}

    def fake_run_tui(args):
        called["provider"] = args.provider
        called["core"] = args.core
        called["timezone"] = args.timezone

    monkeypatch.setattr(cli, "run_tui_from_args", fake_run_tui)

    cli.main(
        [
            "--home",
            str(tmp_path / "home"),
            "--provider",
            "fake",
            "--core",
            "assistant",
            "--timezone",
            "Asia/Shanghai",
        ]
    )

    assert called == {"provider": "fake", "core": "assistant", "timezone": "Asia/Shanghai"}


def test_cli_uses_host_config_defaults_for_tui(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.yaml").write_text(
        "runtime:\n  default_core: evolver\nchannel:\n  busy_mode: queue\n",
        encoding="utf-8",
    )
    called = {}

    def fake_run_tui(args):
        called["core"] = args.core
        called["workspace"] = args.workspace
        called["busy_mode"] = args.channel_busy_mode

    monkeypatch.setattr(cli, "run_tui_from_args", fake_run_tui)

    cli.main(["--home", str(home), "--provider", "fake"])

    assert called == {"core": "evolver", "workspace": None, "busy_mode": "queue"}


def test_cli_core_and_workspace_override_host_config(monkeypatch, tmp_path):
    home = tmp_path / "home"
    cli_workspace = tmp_path / "cli-workspace"
    home.mkdir()
    cli_workspace.mkdir()
    (home / "config.yaml").write_text(
        "runtime:\n  default_core: evolver\n",
        encoding="utf-8",
    )
    called = {}

    def fake_run_tui(args):
        called["core"] = args.core
        called["workspace"] = args.workspace

    monkeypatch.setattr(cli, "run_tui_from_args", fake_run_tui)

    cli.main(["--home", str(home), "--core", "assistant", "--workspace", str(cli_workspace)])

    assert called == {"core": "assistant", "workspace": cli_workspace}


def test_cli_workspace_env_overrides_host_config(monkeypatch, tmp_path):
    home = tmp_path / "home"
    env_workspace = tmp_path / "env-workspace"
    home.mkdir()
    env_workspace.mkdir()
    monkeypatch.setenv("DEMIURGE_WORKSPACE", str(env_workspace))
    called = {}

    def fake_run_tui(args):
        called["workspace"] = args.workspace

    monkeypatch.setattr(cli, "run_tui_from_args", fake_run_tui)

    cli.main(["--home", str(home)])

    assert called == {"workspace": None}


def test_tui_gateway_config_uses_launch_cwd_as_workspace_fallback(monkeypatch, tmp_path):
    launch = tmp_path / "launch"
    launch.mkdir()
    monkeypatch.chdir(launch)
    args = cli.build_parser().parse_args(
        ["--home", str(tmp_path / "home"), "--provider", "fake", "--timezone", "Asia/Shanghai"]
    )
    cli._apply_host_config_defaults(args)

    config = tui_launcher._gateway_config(args)

    assert config["workspace"] is None
    assert config["workspace_fallback"] == str(launch.resolve())
    assert config["timezone"] == "Asia/Shanghai"


def test_gateway_subcommand_runs_gateway(monkeypatch, tmp_path):
    called = {}

    def fake_create_app(**kwargs):
        called["create_app"] = kwargs
        return object()

    def fake_gateway(app):
        called["gateway_app"] = app

    monkeypatch.setattr(cli, "create_app", fake_create_app)
    monkeypatch.setattr(cli, "run_gateway", fake_gateway)

    cli.main(["gateway", "--home", str(tmp_path / "home"), "--provider", "fake", "--timezone", "Asia/Shanghai"])

    assert called["create_app"]["provider_name"] == "fake"
    assert called["create_app"]["timezone"] == "Asia/Shanghai"
    assert called["gateway_app"] is not None


def test_gateway_subcommand_reports_config_error(monkeypatch, tmp_path):
    def fake_create_app(**kwargs):
        return object()

    def fake_gateway(app):
        raise cli.GatewayConfigError("core `assistant` has no enabled gateway channels")

    monkeypatch.setattr(cli, "create_app", fake_create_app)
    monkeypatch.setattr(cli, "run_gateway", fake_gateway)

    with pytest.raises(SystemExit) as exc:
        cli.main(["gateway", "--home", str(tmp_path / "home")])

    assert str(exc.value) == "core `assistant` has no enabled gateway channels"


def test_core_cli_status_check_versions_and_rollback(tmp_path, capsys):
    home = tmp_path / "home"
    init_runtime(home=home, agents_root=source_agents_root())
    store = VersionStore(home)
    original = store.core_repository.live_revision()
    soul = store.active_core_path("assistant") / "agent" / "SOUL.md"
    soul.write_text(soul.read_text(encoding="utf-8") + "\n\nCLI rollback setup.\n", encoding="utf-8")
    changed = store.core_repository.commit_live(reason="test setup", summary="test setup")

    cli.main(["--home", str(home), "core"])
    bare_status_output = capsys.readouterr().out
    assert "agents_root:" in bare_status_output
    assert changed.revision[:12] in bare_status_output

    cli.main(["--home", str(home), "core", "status"])
    status_output = capsys.readouterr().out
    assert "agents_root:" in status_output
    assert changed.revision[:12] in status_output

    cli.main(["--home", str(home), "core", "check"])
    check_output = capsys.readouterr().out
    assert "[ok] path_safety" in check_output

    cli.main(["--home", str(home), "core", "versions", "--limit", "2"])
    versions_output = capsys.readouterr().out
    assert changed.revision in versions_output
    assert original in versions_output

    cli.main(["--home", str(home), "core", "rollback"])
    rollback_output = capsys.readouterr().out
    assert "rollback committed:" in rollback_output
    assert "CLI rollback setup." not in soul.read_text(encoding="utf-8")


def test_core_cli_status_shows_repository_consistency_issues(tmp_path, capsys):
    home = tmp_path / "home"
    init_runtime(home=home, agents_root=source_agents_root())
    store = VersionStore(home)
    live = store.core_repository.live_revision()
    store.core_repository._run_git(["update-ref", "refs/demiurge/previous", live])

    cli.main(["--home", str(home), "core", "status"])

    output = capsys.readouterr().out
    assert "consistency:" in output
    assert "core.previous_ref_matches_live" in output
    assert "demiurge core status" in output


def test_core_cli_save_diff_and_discard_local_edits(tmp_path, capsys):
    home = tmp_path / "home"
    init_runtime(home=home, agents_root=source_agents_root())
    store = VersionStore(home)
    soul = store.active_core_path("assistant") / "agent" / "SOUL.md"
    original = soul.read_text(encoding="utf-8")
    soul.write_text(original + "\n\nCLI local edit.\n", encoding="utf-8")

    cli.main(["--home", str(home), "core", "diff"])
    diff_output = capsys.readouterr().out
    assert "CLI local edit." in diff_output

    cli.main(["--home", str(home), "core", "save"])
    save_output = capsys.readouterr().out
    assert "saved local agent edits:" in save_output
    assert "save assistant authored prompt edits" in save_output
    assert store.core_repository.live_changed_paths() == []

    soul.write_text(soul.read_text(encoding="utf-8") + "\nDiscard me.\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="discard requires --yes"):
        cli.main(["--home", str(home), "core", "discard"])

    cli.main(["--home", str(home), "core", "discard", "--yes"])
    discard_output = capsys.readouterr().out
    assert "discarded local agent edits:" in discard_output
    assert "Discard me." not in soul.read_text(encoding="utf-8")


def test_cli_help_does_not_expose_channel_flag():
    removed_flag = "--" + "channel"
    assert removed_flag not in cli.build_parser().format_help()


def test_cli_help_does_not_expose_root_base_url_or_api_key():
    help_text = cli.build_parser().format_help()

    assert "--base-url" not in help_text
    assert "--api-key" not in help_text


def test_setup_provider_add_writes_host_config(tmp_path, capsys):
    home = tmp_path / "home"

    cli.main(
        [
            "--home",
            str(home),
            "setup",
            "providers",
            "add",
            "deepseek",
            "--preset",
            "deepseek",
            "--set-default",
            "--json",
        ]
    )

    output = capsys.readouterr().out
    assert '"provider": "deepseek"' in output
    assert '"base_url_source": "builtin:deepseek.base_url"' in output
    raw = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
    assert raw["providers"]["default"] == "deepseek"
    assert raw["providers"]["builtin"] == {}
    assert raw["providers"]["custom"] == {}


def test_setup_provider_add_accepts_explicit_api_mode_for_custom_provider(tmp_path, capsys):
    home = tmp_path / "home"

    cli.main(
        [
            "--home",
            str(home),
            "setup",
            "providers",
            "add",
            "claude",
            "--api-mode",
            "anthropic-messages",
            "--base-url",
            "https://api.anthropic.com/v1",
            "--api-key-env",
            "ANTHROPIC_API_KEY",
            "--json",
        ]
    )

    output = capsys.readouterr().out
    assert '"api_mode": "anthropic-messages"' in output
    raw = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
    assert raw["providers"]["custom"]["claude"]["api_mode"] == "anthropic-messages"


def test_setup_provider_add_rejects_builtin_api_mode_override(tmp_path):
    home = tmp_path / "home"

    with pytest.raises(SystemExit, match="--api-mode is only supported for custom provider profiles"):
        cli.main(
            [
                "--home",
                str(home),
                "setup",
                "providers",
                "add",
                "deepseek",
                "--preset",
                "deepseek",
                "--api-mode",
                "anthropic-messages",
            ]
        )


def test_setup_provider_add_rejects_builtin_secret_env_override(tmp_path):
    home = tmp_path / "home"

    with pytest.raises(SystemExit, match="--api-key-env is only supported for custom provider profiles"):
        cli.main(
            [
                "--home",
                str(home),
                "setup",
                "providers",
                "add",
                "deepseek",
                "--preset",
                "deepseek",
                "--api-key-env",
                "DEEPSEEK_PROXY_API_KEY",
            ]
        )


def test_setup_provider_add_writes_builtin_secret_to_official_env(tmp_path, capsys):
    home = tmp_path / "home"

    cli.main(
        [
            "--home",
            str(home),
            "setup",
            "providers",
            "add",
            "openai",
            "--preset",
            "openai",
            "--api-key",
            "secret-value",
            "--write-env",
            "--json",
        ]
    )

    capsys.readouterr()
    raw = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
    assert raw["providers"]["builtin"] == {}
    assert 'OPENAI_API_KEY="secret-value"' in (home / ".env").read_text(encoding="utf-8")


def test_setup_model_set_commits_core_revision_and_leaves_live_clean(tmp_path):
    home = tmp_path / "home"
    context = setup_cli.load_setup_context(home)

    result = setup_cli.set_core_model(context, core_id="assistant", provider_id="fake", model_name="fake/custom")

    assert result["revision"]
    store = VersionStore(home)
    assert store.core_repository.live_changed_paths() == []
    raw = yaml.safe_load((store.active_core_path("assistant") / "agent.yaml").read_text(encoding="utf-8"))
    assert raw["model"]["provider"] == "fake"
    assert raw["model"]["model_name"] == "fake/custom"
    subject = store.core_repository._run_git(["log", "-1", "--format=%s"]).stdout.strip()
    assert subject == "update assistant model config"


def test_setup_timezone_set_and_clear_writes_host_config(tmp_path, capsys):
    home = tmp_path / "home"

    cli.main(["--home", str(home), "setup", "timezone", "set", "Asia/Shanghai", "--json"])
    output = capsys.readouterr().out
    assert '"runtime_timezone": "Asia/Shanghai"' in output
    raw = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
    assert raw["runtime"]["timezone"] == "Asia/Shanghai"

    cli.main(["--home", str(home), "setup", "timezone", "clear", "--json"])
    output = capsys.readouterr().out
    assert '"runtime_timezone_source"' in output
    raw = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
    assert raw["runtime"]["timezone"] is None


def test_setup_wizard_add_provider_sets_core_model_with_preset_default(tmp_path):
    home = tmp_path / "home"
    context = setup_cli.load_setup_context(home)
    prompt = _FakeSetupPrompt(
        selections=["add-provider", "deepseek", "exit"],
        inputs=["", "", "", "", "", ""],
        confirms=[True],
    )

    setup_cli.run_setup_wizard(
        context,
        console=Console(file=StringIO()),
        prompt=prompt,
    )

    host_config = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
    core_model = yaml.safe_load((home / "agents" / "assistant" / "agent.yaml").read_text(encoding="utf-8"))["model"]
    assert host_config["providers"]["default"] == "deepseek"
    assert host_config["providers"]["builtin"] == {}
    assert core_model["provider"] == "deepseek"
    assert core_model["model_name"] == "deepseek-v4-pro"
    assert prompt.input_defaults[-1] == "deepseek-v4-pro"


def test_setup_wizard_custom_provider_requires_explicit_model(tmp_path):
    home = tmp_path / "home"
    context = setup_cli.load_setup_context(home)
    prompt = _FakeSetupPrompt(
        selections=["add-provider", "custom", "exit"],
        inputs=[
            "CustomAPI",
            "https://llm.example.test/v1",
            "CUSTOM_API_KEY",
            "",
            "",
            "custom-model",
        ],
        confirms=[False],
    )

    setup_cli.run_setup_wizard(
        context,
        console=Console(file=StringIO()),
        prompt=prompt,
    )

    core_model = yaml.safe_load((home / "agents" / "assistant" / "agent.yaml").read_text(encoding="utf-8"))["model"]
    host_config = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
    assert host_config["providers"]["custom"]["customapi"]["base_url"] == "https://llm.example.test/v1"
    assert core_model["provider"] == "customapi"
    assert core_model["model_name"] == "custom-model"
    assert prompt.input_defaults[-1] is None


def test_domestic_provider_presets_use_latest_flagship_defaults():
    assert get_provider_preset("deepseek").suggested_model == "deepseek-v4-pro"
    assert get_provider_preset("moonshot").suggested_model == "kimi-k2.7-code"
    assert get_provider_preset("minimax").suggested_model == "MiniMax-M3"
    assert get_provider_preset("minimax").runtime_profile.base_url == "https://api.minimax.io/anthropic"
    assert get_provider_preset("minimax").runtime_profile.api_mode == "anthropic-messages"
    assert get_provider_preset("minimax-cn").runtime_profile.base_url == "https://api.minimaxi.com/anthropic"
    assert get_provider_preset("minimax-cn").runtime_profile.env_vars[0] == "MINIMAX_CN_API_KEY"
    assert get_provider_preset("minimax-cn").suggested_model == "MiniMax-M3"
    assert get_provider_preset("dashscope").suggested_model == "qwen3.7-max"
    assert get_provider_preset("zai").suggested_model == "glm-5.2"
    assert get_provider_preset("siliconflow").suggested_model is None


def test_setup_provider_add_normalizes_profile_id(tmp_path, capsys):
    home = tmp_path / "home"

    cli.main(
        [
            "--home",
            str(home),
            "setup",
            "providers",
            "add",
            "CustomAPI",
            "--base-url",
            "https://llm.example.test/v1",
            "--api-key-env",
            "CUSTOM_API_KEY",
            "--set-default",
            "--json",
        ]
    )

    output = capsys.readouterr().out
    assert '"provider": "customapi"' in output
    raw = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
    assert raw["providers"]["default"] == "customapi"
    assert raw["providers"]["custom"]["customapi"]["base_url"] == "https://llm.example.test/v1"
    assert "CustomAPI" not in raw["providers"]["custom"]


def test_setup_provider_write_env_keeps_secret_out_of_config(tmp_path, capsys):
    home = tmp_path / "home"

    cli.main(
        [
            "--home",
            str(home),
            "setup",
            "providers",
            "add",
            "custom-one",
            "--base-url",
            "https://custom.example.test/v1",
            "--api-key-env",
            "CUSTOM_ONE_API_KEY",
            "--api-key",
            "secret-value",
            "--write-env",
            "--json",
        ]
    )

    output = capsys.readouterr().out
    assert '"api_key_source": "env:CUSTOM_ONE_API_KEY"' in output
    raw = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
    assert raw["providers"]["custom"]["custom-one"]["api_key"] is None
    assert 'CUSTOM_ONE_API_KEY="secret-value"' in (home / ".env").read_text(encoding="utf-8")


def test_setup_provider_edit_preserves_existing_fields(tmp_path):
    home = tmp_path / "home"

    cli.main(
        [
            "--home",
            str(home),
            "setup",
            "providers",
            "add",
            "custom-one",
            "--base-url",
            "https://custom.example.test/v1",
            "--api-key-env",
            "CUSTOM_ONE_API_KEY",
        ]
    )
    cli.main(
        [
            "--home",
            str(home),
            "setup",
            "providers",
            "edit",
            "custom-one",
            "--api-key-env",
            "CUSTOM_TWO_API_KEY",
        ]
    )

    raw = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
    profile = raw["providers"]["custom"]["custom-one"]
    assert profile["base_url"] == "https://custom.example.test/v1"
    assert profile["api_key_env"] == "CUSTOM_TWO_API_KEY"


def test_setup_provider_status_reports_direct_key_when_env_missing(monkeypatch):
    monkeypatch.delenv("CUSTOM_ONE_API_KEY", raising=False)
    host_config = setup_cli.HostConfig(
        providers={
            "custom": {
                "custom-one": {
                    "base_url": "https://custom.example.test/v1",
                    "api_key_env": "CUSTOM_ONE_API_KEY",
                    "api_key": "direct-secret",
                }
            }
        }
    )
    profile = setup_cli.resolve_host_provider_profile(host_config, "custom-one")

    assert setup_cli.provider_profile_dict(profile)["api_key_source"] == "config.yaml:providers.custom.custom-one.api_key"


def test_setup_model_set_updates_runtime_core_without_legacy_model_keys(tmp_path):
    home = tmp_path / "home"

    cli.main(
        [
            "--home",
            str(home),
            "setup",
            "providers",
            "add",
            "CustomAPI",
            "--base-url",
            "https://llm.example.test/v1",
        ]
    )

    cli.main(
        [
            "--home",
            str(home),
            "setup",
            "model",
            "set",
            "--core",
            "assistant",
            "--provider",
            "CustomAPI",
            "--model",
            "custom-model",
            "--json",
        ]
    )

    raw = yaml.safe_load((home / "agents" / "assistant" / "agent.yaml").read_text(encoding="utf-8"))
    assert raw["model"]["provider"] == "customapi"
    assert raw["model"]["model_name"] == "custom-model"
    for key in ("model_name_env", "base_url", "base_url_env", "api_key", "api_key_env"):
        assert key not in raw["model"]


def test_setup_provider_test_is_explicit_and_mockable(monkeypatch, tmp_path, capsys):
    home = tmp_path / "home"
    (home).mkdir()
    (home / "config.yaml").write_text(
        "providers:\n"
        "  custom:\n"
        "    local:\n"
        "      base_url: https://local.example.test/v1\n"
        "      api_key: direct-key\n",
        encoding="utf-8",
    )

    class FakeProvider:
        async def complete(self, request):
            assert request.model == "test-model"
            return LLMResponse(content="ok")

    def fake_create_provider(**kwargs):
        return FakeProvider(), kwargs["provider_config"].provider_id

    monkeypatch.setattr(setup_cli, "create_provider", fake_create_provider)

    cli.main(["--home", str(home), "setup", "providers", "test", "local", "--model", "test-model", "--json"])

    output = capsys.readouterr().out
    assert '"ok": true' in output
    assert '"response": "ok"' in output


class _FakeSetupPrompt:
    def __init__(
        self,
        *,
        selections: list[str] | None = None,
        inputs: list[str] | None = None,
        confirms: list[bool] | None = None,
    ) -> None:
        self.selections = list(selections or [])
        self.inputs = list(inputs or [])
        self.confirms = list(confirms or [])
        self.input_defaults: list[str | None] = []

    def select(self, title, choices, *, default_index=0):
        if self.selections:
            return self.selections.pop(0)
        return choices[default_index].value

    def confirm(self, message, *, default=False):
        if self.confirms:
            return self.confirms.pop(0)
        return default

    def input(self, message, *, default=None, secret=False):
        self.input_defaults.append(default)
        if not self.inputs:
            return default or ""
        value = self.inputs.pop(0)
        return default or "" if value == "" else value


def test_update_uses_default_managed_checkout(monkeypatch, tmp_path, capsys):
    home = tmp_path / "home"
    install_dir = home / "demiurge-agent"
    (install_dir / ".git").mkdir(parents=True)
    calls = []

    def fake_run(command, *, cwd, check):
        calls.append((command, cwd, check))

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    cli.main(["--home", str(home), "update"])

    assert calls == [
        (["git", "fetch", "--all", "--prune"], install_dir.resolve(), True),
        (["git", "pull", "--ff-only"], install_dir.resolve(), True),
        (["uv", "sync"], install_dir.resolve(), True),
        (["uv", "run", "demiurge", "init", "--home", str(home.resolve()), "--check"], install_dir.resolve(), True),
    ]
    assert "updated managed checkout" in capsys.readouterr().out


def test_update_ref_checkout_skips_pull(monkeypatch, tmp_path):
    home = tmp_path / "home"
    install_dir = tmp_path / "custom"
    (install_dir / ".git").mkdir(parents=True)
    calls = []

    def fake_run(command, *, cwd, check):
        calls.append(command)

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    cli.main(["update", "--home", str(home), "--install-dir", str(install_dir), "--ref", "v0.1.0", "--skip-init-check"])

    assert calls == [
        ["git", "fetch", "--all", "--prune"],
        ["git", "checkout", "v0.1.0"],
        ["uv", "sync"],
    ]


def test_update_requires_managed_checkout(tmp_path):
    with pytest.raises(SystemExit) as exc:
        cli.main(["update", "--home", str(tmp_path / "home")])

    assert "managed checkout not found" in str(exc.value)


def test_tui_launcher_prefers_repo_dist(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    entry = repo / "ui-tui" / "dist" / "entry.js"
    entry.parent.mkdir(parents=True)
    entry.write_text("console.log('repo')\n")

    monkeypatch.setattr(tui_launcher.Path, "resolve", lambda self: repo / "demiurge" / "ui" / "tui_launcher.py")
    monkeypatch.setattr(tui_launcher, "_packaged_tui_entry", lambda: tmp_path / "package" / "entry.js")

    resolved, command = tui_launcher._resolve_tui_entry("/usr/bin/node")

    assert resolved == entry
    assert command == ["/usr/bin/node", str(entry)]


def test_tui_01_default_launcher_ignores_stale_repo_dist(monkeypatch, tmp_path):
    """TUI-01: default launch selects the tracked package without mutating bundles."""
    repo = tmp_path / "repo"
    stale_entry = repo / "ui-tui" / "dist" / "entry.js"
    stale_entry.parent.mkdir(parents=True)
    stale_entry.write_text("console.log('stale interaction protocol')\n", encoding="utf-8")
    packaged_entry = tmp_path / "package" / "entry.js"
    packaged_entry.parent.mkdir(parents=True)
    packaged_entry.write_text("console.log('tracked operator protocol')\n", encoding="utf-8")
    bundle_snapshot = {
        stale_entry: stale_entry.read_bytes(),
        packaged_entry: packaged_entry.read_bytes(),
    }
    launched = {}

    def fake_run(command, *, env):
        launched["command"] = command
        launched["env"] = env
        return subprocess.CompletedProcess(command, 0)

    module_path = tui_launcher.Path(tui_launcher.__file__)
    original_resolve = tui_launcher.Path.resolve

    # Model source-checkout and package-resource roots without touching either real bundle.
    def fake_resolve(path, *args, **kwargs):
        if path == module_path:
            return repo / "demiurge" / "ui" / "tui_launcher.py"
        return original_resolve(path, *args, **kwargs)

    class FakePackageResources:
        def joinpath(self, name):
            assert name == "entry.js"
            return packaged_entry

    monkeypatch.setattr(tui_launcher.Path, "resolve", fake_resolve)
    monkeypatch.setattr(tui_launcher, "files", lambda package: FakePackageResources())
    monkeypatch.setattr(tui_launcher.shutil, "which", lambda executable: "/usr/bin/node")
    monkeypatch.setattr(tui_launcher.subprocess, "run", fake_run)
    args = cli.build_parser().parse_args(["--home", str(tmp_path / "home"), "--provider", "fake"])

    tui_launcher.run_tui_from_args(args)

    assert {path: path.read_bytes() for path in bundle_snapshot} == bundle_snapshot
    assert launched["command"] == ["/usr/bin/node", str(packaged_entry)]


def test_tui_launcher_uses_packaged_dist_when_repo_dist_missing(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    packaged = tmp_path / "package" / "entry.js"
    packaged.parent.mkdir(parents=True)
    packaged.write_text("console.log('package')\n")

    monkeypatch.setattr(tui_launcher.Path, "resolve", lambda self: repo / "demiurge" / "ui" / "tui_launcher.py")
    monkeypatch.setattr(tui_launcher, "_packaged_tui_entry", lambda: packaged)

    resolved, command = tui_launcher._resolve_tui_entry("/usr/bin/node")

    assert resolved == packaged
    assert command == ["/usr/bin/node", str(packaged)]
