import pytest

from demiurge import cli
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
    workspace = tmp_path / "workspace"
    home.mkdir()
    workspace.mkdir()
    (home / "config.yaml").write_text(
        f"runtime:\n  default_core: evolver\n  workspace: {workspace}\nchannel:\n  busy_mode: queue\n",
        encoding="utf-8",
    )
    called = {}

    def fake_run_tui(args):
        called["core"] = args.core
        called["workspace"] = args.workspace
        called["busy_mode"] = args.channel_busy_mode

    monkeypatch.setattr(cli, "run_tui_from_args", fake_run_tui)

    cli.main(["--home", str(home), "--provider", "fake"])

    assert called == {"core": "evolver", "workspace": workspace, "busy_mode": "queue"}


def test_cli_core_and_workspace_override_host_config(monkeypatch, tmp_path):
    home = tmp_path / "home"
    configured_workspace = tmp_path / "configured-workspace"
    cli_workspace = tmp_path / "cli-workspace"
    home.mkdir()
    configured_workspace.mkdir()
    cli_workspace.mkdir()
    (home / "config.yaml").write_text(
        f"runtime:\n  default_core: evolver\n  workspace: {configured_workspace}\n",
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
    configured_workspace = tmp_path / "configured-workspace"
    env_workspace = tmp_path / "env-workspace"
    home.mkdir()
    configured_workspace.mkdir()
    env_workspace.mkdir()
    (home / "config.yaml").write_text(f"runtime:\n  workspace: {configured_workspace}\n", encoding="utf-8")
    monkeypatch.setenv("DEMIURGE_WORKSPACE", str(env_workspace))
    called = {}

    def fake_run_tui(args):
        called["workspace"] = args.workspace

    monkeypatch.setattr(cli, "run_tui_from_args", fake_run_tui)

    cli.main(["--home", str(home)])

    assert called == {"workspace": None}


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
