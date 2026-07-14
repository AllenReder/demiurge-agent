from __future__ import annotations

import json
import os
import stat
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest

from demiurge.app import create_app, init_runtime, source_agents_root
from demiurge.security.private_files import (
    RuntimePermissionError,
    append_private_jsonl,
    audit_runtime_permissions,
    ensure_private_directory,
    ensure_private_file,
    open_private_text,
    tighten_runtime_permissions,
)
from demiurge.storage import ArtifactStore, EventLog
from demiurge.util import atomic_write_private_text


@contextmanager
def _umask(value: int):
    previous = os.umask(value)
    try:
        yield
    finally:
        os.umask(previous)


@pytest.mark.skipif(os.name == "nt", reason="Windows uses platform ACL semantics")
def test_private_runtime_writes_ignore_permissive_umask(tmp_path):
    home = tmp_path / "home"
    event_path = home / "runtime" / "session-events" / "session.jsonl"
    log_path = home / "logs" / "mcp-stderr.log"

    with _umask(0):
        ensure_private_directory(home)
        append_private_jsonl(event_path, {"token": "already-redacted"})
        with open_private_text(log_path, "a", encoding="utf-8") as handle:
            handle.write("safe log\n")

    assert stat.S_IMODE(home.stat().st_mode) == 0o700
    assert stat.S_IMODE((home / "runtime").stat().st_mode) == 0o700
    assert stat.S_IMODE(event_path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(event_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(log_path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(log_path.stat().st_mode) == 0o600
    assert json.loads(event_path.read_text(encoding="utf-8")) == {
        "token": "already-redacted"
    }


@pytest.mark.skipif(os.name == "nt", reason="Windows uses platform ACL semantics")
def test_private_directory_rejects_existing_symlink_ancestor(tmp_path):
    home = tmp_path / "home"
    outside = tmp_path / "outside"
    ensure_private_directory(home)
    outside_artifacts = outside / "artifacts"
    outside_artifacts.mkdir(parents=True)
    os.chmod(outside_artifacts, 0o755)
    (home / "runtime").symlink_to(outside, target_is_directory=True)

    with pytest.raises(RuntimePermissionError, match="symbolic links"):
        ensure_private_directory(home / "runtime" / "artifacts")

    assert stat.S_IMODE(outside_artifacts.stat().st_mode) == 0o755


@pytest.mark.skipif(
    os.name == "nt" or not hasattr(os, "O_NOFOLLOW"),
    reason="requires POSIX directory descriptors",
)
def test_private_directory_creation_does_not_escape_after_ancestor_swap(
    monkeypatch,
    tmp_path,
):
    home = tmp_path / "home"
    stashed_home = tmp_path / "stashed-home"
    outside = tmp_path / "outside"
    home.mkdir()
    outside.mkdir()
    real_mkdir = os.mkdir
    swapped = False

    def race_mkdir(path, mode=0o777, *, dir_fd=None):
        nonlocal swapped
        if not swapped and os.fspath(path).endswith("runtime"):
            home.rename(stashed_home)
            home.symlink_to(outside, target_is_directory=True)
            swapped = True
        return real_mkdir(path, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "mkdir", race_mkdir)

    with pytest.raises((OSError, RuntimePermissionError)):
        ensure_private_directory(home / "runtime" / "artifacts")

    assert swapped is True
    assert not (outside / "runtime").exists()


@pytest.mark.skipif(
    os.name == "nt" or not hasattr(os, "O_NOFOLLOW"),
    reason="requires POSIX directory descriptors",
)
def test_private_directory_creation_tolerates_concurrent_creator(
    monkeypatch,
    tmp_path,
):
    home = tmp_path / "home"
    home.mkdir()
    runtime = home / "runtime"
    real_mkdir = os.mkdir
    both_creators_ready = threading.Barrier(2)

    def synchronized_mkdir(path, mode=0o777, *, dir_fd=None):
        if os.fspath(path).endswith(runtime.name):
            both_creators_ready.wait(timeout=5)
        return real_mkdir(path, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "mkdir", synchronized_mkdir)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                ensure_private_directory,
                (runtime, runtime),
            )
        )

    assert results == [runtime, runtime]
    assert stat.S_IMODE(runtime.stat().st_mode) == 0o700


@pytest.mark.skipif(
    os.name == "nt" or not hasattr(os, "O_NOFOLLOW"),
    reason="requires POSIX O_NOFOLLOW",
)
def test_private_file_open_does_not_follow_racing_symlink(monkeypatch, tmp_path):
    private_path = tmp_path / "home" / "runtime" / "result.txt"
    outside = tmp_path / "outside.txt"
    outside.write_text("outside sentinel\n", encoding="utf-8")
    real_open = os.open

    def race_open(path, flags, mode=0o777, *, dir_fd=None):
        if (
            flags & os.O_CREAT
            and os.fspath(path).endswith(private_path.name)
        ):
            private_path.symlink_to(outside)
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", race_open)

    with pytest.raises(OSError):
        with open_private_text(private_path, "w", encoding="utf-8") as handle:
            handle.write("private data\n")

    assert outside.read_text(encoding="utf-8") == "outside sentinel\n"


@pytest.mark.skipif(
    os.name == "nt" or not hasattr(os, "O_NOFOLLOW"),
    reason="requires POSIX directory descriptors",
)
def test_private_file_open_does_not_escape_after_parent_swap(
    monkeypatch,
    tmp_path,
):
    home = tmp_path / "home"
    runtime = home / "runtime"
    stashed_runtime = home / "stashed-runtime"
    outside = tmp_path / "outside"
    runtime.mkdir(parents=True)
    outside.mkdir()
    target = runtime / "result.txt"
    outside_target = outside / target.name
    outside_target.write_text("outside sentinel\n", encoding="utf-8")
    real_open = os.open
    swapped = False

    def race_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal swapped
        if (
            not swapped
            and flags & os.O_CREAT
            and os.fspath(path).endswith(target.name)
        ):
            runtime.rename(stashed_runtime)
            runtime.symlink_to(outside, target_is_directory=True)
            swapped = True
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", race_open)

    with pytest.raises((OSError, RuntimePermissionError)):
        with open_private_text(target, "w", encoding="utf-8") as handle:
            handle.write("private data\n")

    assert swapped is True
    assert outside_target.read_text(encoding="utf-8") == "outside sentinel\n"


@pytest.mark.skipif(
    os.name == "nt" or not hasattr(os, "O_NOFOLLOW"),
    reason="requires POSIX directory descriptors",
)
def test_atomic_private_write_does_not_escape_after_parent_swap(
    monkeypatch,
    tmp_path,
):
    home = tmp_path / "home"
    runtime = home / "runtime"
    stashed_runtime = home / "stashed-runtime"
    outside = tmp_path / "outside"
    runtime.mkdir(parents=True)
    outside.mkdir()
    target = runtime / "state.json"
    outside_target = outside / target.name
    outside_target.write_text("outside sentinel\n", encoding="utf-8")
    real_open = os.open
    swapped = False

    def race_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal swapped
        if (
            not swapped
            and flags & os.O_EXCL
            and f".{target.name}." in os.fspath(path)
        ):
            runtime.rename(stashed_runtime)
            runtime.symlink_to(outside, target_is_directory=True)
            swapped = True
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", race_open)

    with pytest.raises((OSError, RuntimePermissionError)):
        atomic_write_private_text(target, "private state\n")

    assert swapped is True
    assert outside_target.read_text(encoding="utf-8") == "outside sentinel\n"


@pytest.mark.skipif(
    os.name == "nt" or not hasattr(os, "O_NOFOLLOW"),
    reason="requires POSIX directory descriptors",
)
def test_private_file_tightening_does_not_chmod_through_swapped_parent(
    monkeypatch,
    tmp_path,
):
    home = tmp_path / "home"
    runtime = home / "runtime"
    stashed_runtime = home / "stashed-runtime"
    outside = tmp_path / "outside"
    runtime.mkdir(parents=True)
    outside.mkdir()
    target = runtime / "state.json"
    target.write_text("private state\n", encoding="utf-8")
    os.chmod(target, 0o644)
    outside_target = outside / target.name
    outside_target.write_text("outside sentinel\n", encoding="utf-8")
    os.chmod(outside_target, 0o644)
    real_chmod = os.chmod

    def race_chmod(path, mode, *args, **kwargs):
        if os.fspath(path) == os.fspath(target):
            runtime.rename(stashed_runtime)
            runtime.symlink_to(outside, target_is_directory=True)
        return real_chmod(path, mode, *args, **kwargs)

    monkeypatch.setattr(os, "chmod", race_chmod)

    ensure_private_file(target)

    assert stat.S_IMODE(outside_target.stat().st_mode) == 0o644


@pytest.mark.skipif(os.name == "nt", reason="Windows uses platform ACL semantics")
def test_existing_runtime_files_are_only_tightened(tmp_path):
    home = tmp_path / "home"
    runtime = home / "runtime"
    artifacts = runtime / "artifacts" / "session" / "artifact"
    logs = home / "logs"
    state = home / "state"
    for directory in (artifacts, logs, state):
        directory.mkdir(parents=True, mode=0o777)
        os.chmod(directory, 0o777)
    paths = [
        home / ".env",
        home / "config.yaml",
        runtime / "runtime.sqlite3",
        artifacts / "stdout.txt",
        logs / "mcp-stderr.log",
        state / "assistant.json",
    ]
    for index, path in enumerate(paths):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"sentinel-{index}\n", encoding="utf-8")
        os.chmod(path, 0o666)
    snapshots = {
        path: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in paths
    }

    failures = tighten_runtime_permissions(home)

    assert failures == ()
    assert audit_runtime_permissions(home) == ()
    for path, (content, mtime_ns) in snapshots.items():
        assert path.read_bytes() == content
        assert path.stat().st_mtime_ns == mtime_ns
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    for directory in (home, runtime, artifacts, logs, state):
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700


@pytest.mark.skipif(os.name == "nt", reason="Windows uses platform ACL semantics")
def test_permission_tightening_failure_returns_secret_safe_issue(monkeypatch, tmp_path):
    home = tmp_path / "home"
    ensure_private_directory(home)
    config = home / "config.yaml"
    config.write_text("api_key: SYNTHETIC_CONFIG_SECRET\n", encoding="utf-8")
    os.chmod(config, 0o644)
    config_identity = (config.stat().st_dev, config.stat().st_ino)
    real_fchmod = os.fchmod

    def fail_config(descriptor, mode):
        metadata = os.fstat(descriptor)
        if (metadata.st_dev, metadata.st_ino) == config_identity:
            raise PermissionError("denied for SYNTHETIC_CONFIG_SECRET")
        return real_fchmod(descriptor, mode)

    monkeypatch.setattr(os, "fchmod", fail_config)

    failures = tighten_runtime_permissions(home)

    assert len(failures) == 1
    assert failures[0].path == str(config)
    assert failures[0].reason == "could not tighten mode (PermissionError)"
    assert "SYNTHETIC_CONFIG_SECRET" not in repr(failures)
    assert config.read_text(encoding="utf-8") == (
        "api_key: SYNTHETIC_CONFIG_SECRET\n"
    )


@pytest.mark.skipif(os.name == "nt", reason="Windows uses platform ACL semantics")
def test_runtime_entrypoints_secure_private_paths_under_permissive_umask(tmp_path):
    home = tmp_path / "home"
    home.mkdir(mode=0o777)
    os.chmod(home, 0o777)
    env_path = home / ".env"
    env_path.write_text('DEMIURGE_SYNTHETIC="secret"\n', encoding="utf-8")
    os.chmod(env_path, 0o666)
    original_env = (env_path.read_bytes(), env_path.stat().st_mtime_ns)

    with _umask(0):
        init_runtime(
            home=home,
            core_id="assistant",
            agents_root=source_agents_root(),
        )
        app = create_app(home=home, provider_name="fake")

    asyncio.run(app.close())

    assert env_path.read_bytes() == original_env[0]
    assert env_path.stat().st_mtime_ns == original_env[1]
    for directory in (home, home / "runtime"):
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    for path in (
        env_path,
        home / "config.yaml",
        home / "runtime" / "runtime.sqlite3",
    ):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.skipif(os.name == "nt", reason="Windows uses platform ACL semantics")
def test_event_log_and_artifact_store_create_private_runtime_files(tmp_path):
    home = tmp_path / "home"
    with _umask(0):
        event_log = EventLog(home, "session-1")
        event_log.emit("probe", value="safe")
        artifact = ArtifactStore(home, "session-1").store(
            {
                "artifact_id": "artifact-1",
                "content": "safe artifact",
                "filename": "result.txt",
            }
        )

    artifact_path = (
        home
        / "runtime"
        / "artifacts"
        / "session-1"
        / str(artifact.path)
    )
    assert stat.S_IMODE(event_log.path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(event_log.path.stat().st_mode) == 0o600
    assert stat.S_IMODE(artifact_path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(artifact_path.stat().st_mode) == 0o600


@pytest.mark.skipif(os.name != "nt", reason="Windows-only ACL contract")
def test_windows_private_file_policy_uses_platform_acl_semantics(tmp_path):
    home = ensure_private_directory(tmp_path / "home")
    path = home / "runtime" / "event.jsonl"

    append_private_jsonl(path, {"value": "safe"})

    assert path.exists()
    assert audit_runtime_permissions(home) == ()
