import pytest
import yaml

from demiurge import cli
from demiurge import setup_cli
from demiurge.providers import LLMResponse
from demiurge.ui import tui_launcher


def test_default_cli_launches_ts_tui(monkeypatch, tmp_path):
    called = {}

    def fake_run_tui(args):
        called["provider"] = args.provider
        called["core"] = args.core

    monkeypatch.setattr(cli, "run_tui_from_args", fake_run_tui)

    cli.main(["--home", str(tmp_path / "home"), "--provider", "fake", "--core", "assistant"])

    assert called == {"provider": "fake", "core": "assistant"}


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
    args = cli.build_parser().parse_args(["--home", str(tmp_path / "home"), "--provider", "fake"])
    cli._apply_host_config_defaults(args)

    config = tui_launcher._gateway_config(args)

    assert config["workspace"] is None
    assert config["workspace_fallback"] == str(launch.resolve())


def test_gateway_subcommand_runs_gateway(monkeypatch, tmp_path):
    called = {}

    def fake_create_app(**kwargs):
        called["create_app"] = kwargs
        return object()

    def fake_gateway(app):
        called["gateway_app"] = app

    monkeypatch.setattr(cli, "create_app", fake_create_app)
    monkeypatch.setattr(cli, "run_gateway", fake_gateway)

    cli.main(["gateway", "--home", str(tmp_path / "home"), "--provider", "fake"])

    assert called["create_app"]["provider_name"] == "fake"
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
    raw = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
    assert raw["providers"]["default"] == "deepseek"
    assert raw["providers"]["profiles"]["deepseek"]["base_url"] == "https://api.deepseek.com"
    assert raw["providers"]["profiles"]["deepseek"]["api_key_env"] == "DEEPSEEK_API_KEY"


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
    assert raw["providers"]["profiles"]["custom-one"]["api_key"] is None
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
    profile = raw["providers"]["profiles"]["custom-one"]
    assert profile["base_url"] == "https://custom.example.test/v1"
    assert profile["api_key_env"] == "CUSTOM_TWO_API_KEY"


def test_setup_provider_status_reports_direct_key_when_env_missing(monkeypatch):
    monkeypatch.delenv("CUSTOM_ONE_API_KEY", raising=False)
    profile = setup_cli.HostProviderProfile(
        base_url="https://custom.example.test/v1",
        api_key_env="CUSTOM_ONE_API_KEY",
        api_key="direct-secret",
    )

    assert setup_cli.provider_profile_dict(profile)["api_key_source"] == "config.yaml:providers.profile.api_key"


def test_setup_model_set_updates_runtime_core_without_legacy_model_keys(tmp_path):
    home = tmp_path / "home"

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
            "deepseek",
            "--model",
            "deepseek-v4-flash",
            "--json",
        ]
    )

    raw = yaml.safe_load((home / "agents" / "assistant" / "agent.yaml").read_text(encoding="utf-8"))
    assert raw["model"]["provider"] == "deepseek"
    assert raw["model"]["model_name"] == "deepseek-v4-flash"
    for key in ("model_name_env", "base_url", "base_url_env", "api_key", "api_key_env"):
        assert key not in raw["model"]


def test_setup_provider_test_is_explicit_and_mockable(monkeypatch, tmp_path, capsys):
    home = tmp_path / "home"
    (home).mkdir()
    (home / "config.yaml").write_text(
        "providers:\n"
        "  profiles:\n"
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
