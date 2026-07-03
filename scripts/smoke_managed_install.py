#!/usr/bin/env python3
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Iterable, NamedTuple


SMOKE_REF = "smoke-managed-install"


class SmokeSource(NamedTuple):
    url: str
    ref: str
    head: str


def path_for_shell(path: os.PathLike[str] | str) -> str:
    return str(path).replace("\\", "/")


def installed_demiurge_path(install_dir: Path, *, is_windows: bool) -> Path:
    if is_windows:
        return install_dir / ".venv" / "Scripts" / "demiurge.exe"
    return install_dir / ".venv" / "bin" / "demiurge"


def smoke_commands(executable: Path, home: Path) -> list[list[str]]:
    command = str(executable)
    runtime_home = str(home)
    return [
        [command, "--home", runtime_home, "--provider", "fake", "--help"],
        [command, "--home", runtime_home, "init", "--check", "--json"],
        [command, "--home", runtime_home, "package", "list", "--json"],
    ]


def install_environment(*, repo_url: str, repo_ref: str, home: Path, install_dir: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.pop("VIRTUAL_ENV", None)
    env.update(
        {
            "DEMIURGE_REPO_URL": repo_url,
            "DEMIURGE_REF": repo_ref,
            "DEMIURGE_HOME": path_for_shell(home),
            "DEMIURGE_INSTALL_DIR": path_for_shell(install_dir),
            "UV_PYTHON": sys.executable,
        }
    )
    return env


def smoke_working_directory(tmp_root: Path) -> Path:
    return tmp_root


def install_shell_candidates(*, is_windows: bool) -> list[str]:
    candidates: list[str | None] = [os.environ.get("BASH")]
    if is_windows:
        candidates.extend(
            [
                r"C:\Program Files\Git\bin\bash.exe",
                r"C:\Program Files\Git\usr\bin\bash.exe",
                shutil.which("bash"),
            ]
        )
    else:
        candidates.extend([shutil.which("bash"), shutil.which("sh")])
    return [candidate for candidate in candidates if candidate]


def find_install_shell(*, is_windows: bool) -> str:
    for candidate in install_shell_candidates(is_windows=is_windows):
        resolved = shutil.which(candidate) or candidate
        if Path(resolved).exists():
            return resolved

    raise RuntimeError("could not find bash/sh for running scripts/install.sh")


def run_checked(command: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> None:
    print(f"+ {' '.join(command)}")
    subprocess.run(command, cwd=cwd, env=env, check=True, timeout=600)


def run_output(command: list[str], *, cwd: Path) -> str:
    print(f"+ {' '.join(command)}")
    return subprocess.check_output(command, cwd=cwd, text=True, timeout=600).strip()


def prepare_smoke_repository(repo_root: Path, tmp_root: Path) -> SmokeSource:
    head = run_output(["git", "rev-parse", "HEAD"], cwd=repo_root)
    bare_repo = tmp_root / "source.git"
    run_checked(["git", "init", "--bare", str(bare_repo)], cwd=tmp_root)
    run_checked(["git", "push", str(bare_repo), f"HEAD:refs/heads/{SMOKE_REF}"], cwd=repo_root)
    run_checked(["git", "symbolic-ref", "HEAD", f"refs/heads/{SMOKE_REF}"], cwd=bare_repo)
    return SmokeSource(url=bare_repo.resolve().as_uri(), ref=SMOKE_REF, head=head)


def verify_installed_revision(install_dir: Path, *, expected_head: str) -> None:
    installed_head = run_output(["git", "rev-parse", "HEAD"], cwd=install_dir)
    if installed_head != expected_head:
        raise RuntimeError(
            "managed install checked out the wrong commit: "
            f"expected {expected_head}, got {installed_head}"
        )


def run_smoke(repo_root: Path) -> None:
    repo_root = repo_root.resolve()
    install_script = repo_root / "scripts" / "install.sh"
    if not install_script.is_file():
        raise RuntimeError(f"missing install script: {install_script}")

    is_windows = sys.platform.startswith("win")
    shell = find_install_shell(is_windows=is_windows)

    with tempfile.TemporaryDirectory(prefix="demiurge-managed-install-") as tmp:
        tmp_root = Path(tmp)
        home = tmp_root / "home"
        install_dir = tmp_root / "demiurge-agent"
        smoke_source = prepare_smoke_repository(repo_root, tmp_root)
        env = install_environment(
            repo_url=smoke_source.url,
            repo_ref=smoke_source.ref,
            home=home,
            install_dir=install_dir,
        )

        run_checked([shell, path_for_shell(install_script)], cwd=repo_root, env=env)
        verify_installed_revision(install_dir, expected_head=smoke_source.head)

        executable = installed_demiurge_path(install_dir, is_windows=is_windows)
        if not executable.is_file():
            raise RuntimeError(f"managed install did not create demiurge command: {executable}")

        for command in smoke_commands(executable, home):
            run_checked(command, cwd=smoke_working_directory(tmp_root))


def main(argv: Iterable[str] | None = None) -> int:
    args = list(argv or [])
    if args:
        print("usage: smoke_managed_install.py", file=sys.stderr)
        return 2
    run_smoke(Path(__file__).resolve().parents[1])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
