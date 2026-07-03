from __future__ import annotations

import importlib.util
import sys
from pathlib import Path, PureWindowsPath


def load_smoke_module():
    script = Path(__file__).resolve().parents[2] / "scripts" / "smoke_managed_install.py"
    spec = importlib.util.spec_from_file_location("smoke_managed_install", script)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_path_for_shell_normalizes_windows_separators():
    smoke = load_smoke_module()

    assert smoke.path_for_shell(PureWindowsPath("C:/tmp/demiurge home")) == "C:/tmp/demiurge home"


def test_installed_demiurge_path_uses_platform_venv_layout():
    smoke = load_smoke_module()
    install_dir = Path("managed-checkout")

    assert smoke.installed_demiurge_path(install_dir, is_windows=True) == Path(
        "managed-checkout/.venv/Scripts/demiurge.exe"
    )
    assert smoke.installed_demiurge_path(install_dir, is_windows=False) == Path("managed-checkout/.venv/bin/demiurge")


def test_smoke_commands_cover_installed_cli_surface():
    smoke = load_smoke_module()
    executable = Path("managed-checkout/.venv/bin/demiurge")
    home = Path("runtime-home")

    commands = smoke.smoke_commands(executable, home)

    assert commands == [
        [str(executable), "--home", str(home), "--provider", "fake", "--help"],
        [str(executable), "--home", str(home), "init", "--check", "--json"],
        [str(executable), "--home", str(home), "package", "list", "--json"],
    ]


def test_install_environment_uses_local_repository_current_python_and_clean_venv(monkeypatch):
    smoke = load_smoke_module()
    monkeypatch.setenv("PATH", "existing-path")
    monkeypatch.setenv("VIRTUAL_ENV", "outer-env")

    env = smoke.install_environment(
        repo_url="file:///tmp/demiurge-source.git",
        repo_ref="smoke-managed-install",
        home=Path("runtime-home"),
        install_dir=Path("managed-checkout"),
    )

    assert env["DEMIURGE_REPO_URL"] == "file:///tmp/demiurge-source.git"
    assert env["DEMIURGE_REF"] == "smoke-managed-install"
    assert env["DEMIURGE_HOME"] == "runtime-home"
    assert env["DEMIURGE_INSTALL_DIR"] == "managed-checkout"
    assert env["UV_PYTHON"] == sys.executable
    assert env["PATH"] == "existing-path"
    assert "VIRTUAL_ENV" not in env


def test_prepare_smoke_repository_exports_current_head_to_dedicated_ref(monkeypatch, tmp_path):
    smoke = load_smoke_module()
    repo_root = Path("source-checkout")
    commands = []

    def fake_run_output(command, *, cwd):
        assert command == ["git", "rev-parse", "HEAD"]
        assert cwd == repo_root
        return "abc123"

    def fake_run_checked(command, *, cwd, env=None):
        commands.append({"command": command, "cwd": cwd, "env": env})

    monkeypatch.setattr(smoke, "run_output", fake_run_output)
    monkeypatch.setattr(smoke, "run_checked", fake_run_checked)

    source = smoke.prepare_smoke_repository(repo_root, tmp_path)

    bare_repo = tmp_path / "source.git"
    assert source.url == bare_repo.resolve().as_uri()
    assert source.ref == "smoke-managed-install"
    assert source.head == "abc123"
    assert commands == [
        {
            "command": ["git", "clone", "--bare", "--no-local", str(repo_root), str(bare_repo)],
            "cwd": tmp_path,
            "env": None,
        },
        {
            "command": ["git", "update-ref", "refs/heads/smoke-managed-install", "abc123"],
            "cwd": bare_repo,
            "env": None,
        },
        {
            "command": ["git", "symbolic-ref", "HEAD", "refs/heads/smoke-managed-install"],
            "cwd": bare_repo,
            "env": None,
        },
    ]


def test_verify_installed_revision_rejects_wrong_commit(monkeypatch):
    smoke = load_smoke_module()

    def fake_run_output(command, *, cwd):
        assert command == ["git", "rev-parse", "HEAD"]
        assert cwd == Path("managed-checkout")
        return "installed456"

    monkeypatch.setattr(smoke, "run_output", fake_run_output)

    try:
        smoke.verify_installed_revision(Path("managed-checkout"), expected_head="source123")
    except RuntimeError as error:
        assert "source123" in str(error)
        assert "installed456" in str(error)
    else:
        raise AssertionError("verify_installed_revision should reject mismatched managed checkout commits")


def test_smoke_working_directory_is_neutral_temp_root():
    smoke = load_smoke_module()

    assert smoke.smoke_working_directory(Path("tmp-root")) == Path("tmp-root")


def test_run_checked_uses_timeout(monkeypatch):
    smoke = load_smoke_module()
    calls = []

    def fake_run(command, *, cwd, env, check, timeout):
        calls.append(
            {
                "command": command,
                "cwd": cwd,
                "env": env,
                "check": check,
                "timeout": timeout,
            }
        )

    monkeypatch.setattr(smoke.subprocess, "run", fake_run)

    smoke.run_checked(["demo"], cwd=Path("work"), env={"A": "B"})

    assert calls == [
        {
            "command": ["demo"],
            "cwd": Path("work"),
            "env": {"A": "B"},
            "check": True,
            "timeout": 600,
        }
    ]


def test_windows_shell_candidates_prefer_git_bash_before_path_bash(monkeypatch):
    smoke = load_smoke_module()
    monkeypatch.setenv("BASH", r"C:\Custom\bash.exe")
    monkeypatch.setattr(smoke.shutil, "which", lambda name: r"C:\Windows\System32\bash.exe" if name == "bash" else None)

    assert smoke.install_shell_candidates(is_windows=True) == [
        r"C:\Custom\bash.exe",
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
        r"C:\Windows\System32\bash.exe",
    ]
