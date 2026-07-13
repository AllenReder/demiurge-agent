from __future__ import annotations

import json
import os
import stat
import threading
from concurrent.futures import ThreadPoolExecutor
from multiprocessing import get_context
from pathlib import Path

import pytest

import demiurge.storage as storage_module
from demiurge.storage import StateConflictError, StateProposal, StateStore


def _matches_at_path(value, directory_descriptor, expected: Path) -> bool:
    path = Path(value)
    if path.is_absolute() or directory_descriptor is None:
        return path == expected
    return path == Path(expected.name)


def _crash_before_proposal_audit_replace(
    home: str,
    forced_proposal_id: str | None = None,
) -> None:
    if forced_proposal_id is not None:
        storage_module.utc_id = lambda _prefix: forced_proposal_id
    store = StateStore.core(Path(home), "assistant")
    original_replace = os.replace

    def crash_on_audit(
        source,
        destination,
        *,
        src_dir_fd=None,
        dst_dir_fd=None,
    ):
        if _matches_at_path(destination, dst_dir_fd, store.proposal_log):
            os._exit(91)
        return original_replace(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )

    os.replace = crash_on_audit
    store.submit(
        StateProposal(target="uncommitted", operation="set", patch=True),
        source="test",
        turn_id="turn_crash",
    )


def test_state_01_concurrent_accepted_proposals_preserve_both_fields(tmp_path, monkeypatch):
    """STATE-01: concurrent accepted proposals must not lose either update."""
    store = StateStore.core(tmp_path, "assistant")
    store.path.parent.mkdir(parents=True)
    store.path.write_text('{"schema_version": 1}\n', encoding="utf-8")
    original_read_text = Path.read_text
    both_submissions_ready = threading.Barrier(2)
    second_read_started = threading.Event()
    read_count_lock = threading.Lock()
    read_count = 0

    def synchronized_read_text(path: Path, *args, **kwargs):
        nonlocal read_count
        content = original_read_text(path, *args, **kwargs)
        if path == store.path:
            with read_count_lock:
                read_count += 1
                current_read = read_count
            if current_read == 1:
                # An unlocked second submit reaches the same snapshot and releases this wait.
                # A correct submit lock instead lets this bounded wait expire before serializing.
                second_read_started.wait(timeout=2)
            else:
                second_read_started.set()
        return content

    def submit(target: str):
        both_submissions_ready.wait(timeout=5)
        independent_store = StateStore.core(tmp_path, "assistant")
        return independent_store.submit(
            StateProposal(target=target, operation="set", patch=True),
            source="test",
            turn_id=f"turn_{target}",
        )

    with monkeypatch.context() as synchronized_filesystem:
        synchronized_filesystem.setattr(Path, "read_text", synchronized_read_text)
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(submit, target) for target in ("left", "right")]
            entries = [future.result(timeout=10) for future in futures]

    assert [entry["accepted"] for entry in entries] == [True, True]
    assert store.snapshot() == {
        "schema_version": 1,
        "left": True,
        "right": True,
    }


def test_state_snapshot_uses_same_directory_atomic_replace_with_private_mode_on_posix(tmp_path, monkeypatch):
    store = StateStore.core(tmp_path, "assistant")
    replace_calls: list[tuple[Path, Path, int | None, int | None]] = []
    original_replace = os.replace

    def recording_replace(
        source,
        destination,
        *,
        src_dir_fd=None,
        dst_dir_fd=None,
    ):
        replace_calls.append(
            (
                Path(source),
                Path(destination),
                src_dir_fd,
                dst_dir_fd,
            )
        )
        return original_replace(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )

    monkeypatch.setattr(os, "replace", recording_replace)
    previous_umask = os.umask(0)
    try:
        store.submit(
            StateProposal(target="private", operation="set", patch=True),
            source="test",
            turn_id="turn_private",
        )
    finally:
        os.umask(previous_umask)

    state_replaces = [
        call
        for call in replace_calls
        if _matches_at_path(call[1], call[3], store.path)
    ]
    assert len(state_replaces) == 1
    temporary_path, destination_path, src_dir_fd, dst_dir_fd = state_replaces[0]
    assert src_dir_fd == dst_dir_fd
    if dst_dir_fd is None:
        assert temporary_path.parent == destination_path.parent == store.path.parent
    else:
        assert temporary_path.parent == destination_path.parent == Path(".")
        assert destination_path == Path(store.path.name)
    assert temporary_path != destination_path
    assert not any(
        candidate.name == temporary_path.name
        for candidate in store.path.parent.iterdir()
    )
    if os.name != "nt":
        assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    assert store.snapshot()["private"] is True


def test_stale_state_revision_is_rejected_without_overwriting_newer_state(tmp_path):
    store = StateStore.core(tmp_path, "assistant")
    initial = store.read_snapshot()

    accepted = store.submit(
        StateProposal(target="newer", operation="set", patch=True),
        source="test",
        turn_id="turn_newer",
        expected_revision=initial.revision,
    )

    with pytest.raises(StateConflictError) as caught:
        store.submit(
            StateProposal(target="stale", operation="set", patch=True),
            source="test",
            turn_id="turn_stale",
            expected_revision=initial.revision,
        )

    current = store.read_snapshot()
    assert caught.value.expected_revision == initial.revision
    assert caught.value.current_revision == accepted["state_revision"]
    assert current.revision == accepted["state_revision"]
    assert current.document == {"schema_version": 1, "newer": True}


def test_audit_write_failure_rolls_back_state_snapshot(tmp_path, monkeypatch):
    store = StateStore.core(tmp_path, "assistant")
    store.submit(
        StateProposal(target="stable", operation="set", patch=True),
        source="test",
        turn_id="turn_stable",
    )
    state_before = store.path.read_bytes()
    audit_before = store.proposal_log.read_bytes()
    original_replace = os.replace

    def fail_proposal_audit(
        source,
        destination,
        *,
        src_dir_fd=None,
        dst_dir_fd=None,
    ):
        if _matches_at_path(destination, dst_dir_fd, store.proposal_log):
            raise OSError("synthetic proposal audit failure")
        return original_replace(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )

    monkeypatch.setattr(os, "replace", fail_proposal_audit)

    with pytest.raises(OSError, match="synthetic proposal audit failure"):
        store.submit(
            StateProposal(target="uncommitted", operation="set", patch=True),
            source="test",
            turn_id="turn_uncommitted",
        )

    assert store.path.read_bytes() == state_before
    assert store.proposal_log.read_bytes() == audit_before
    assert store.snapshot() == {"schema_version": 1, "stable": True}


def test_proposal_audit_uses_atomic_replace_with_private_mode_on_posix(tmp_path, monkeypatch):
    store = StateStore.core(tmp_path, "assistant")
    replace_calls: list[tuple[Path, Path, int | None, int | None]] = []
    original_replace = os.replace

    def recording_replace(
        source,
        destination,
        *,
        src_dir_fd=None,
        dst_dir_fd=None,
    ):
        replace_calls.append(
            (
                Path(source),
                Path(destination),
                src_dir_fd,
                dst_dir_fd,
            )
        )
        return original_replace(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )

    monkeypatch.setattr(os, "replace", recording_replace)
    previous_umask = os.umask(0)
    try:
        store.submit(
            StateProposal(target="audited", operation="set", patch=True),
            source="test",
            turn_id="turn_audited",
        )
    finally:
        os.umask(previous_umask)

    audit_replaces = [
        call
        for call in replace_calls
        if _matches_at_path(call[1], call[3], store.proposal_log)
    ]
    assert len(audit_replaces) == 1
    temporary_path, destination_path, src_dir_fd, dst_dir_fd = audit_replaces[0]
    assert src_dir_fd == dst_dir_fd
    if dst_dir_fd is None:
        assert temporary_path.parent == destination_path.parent == store.proposal_log.parent
    else:
        assert temporary_path.parent == destination_path.parent == Path(".")
        assert destination_path == Path(store.proposal_log.name)
    assert temporary_path != destination_path
    assert not any(
        candidate.name == temporary_path.name
        for candidate in store.proposal_log.parent.iterdir()
    )
    if os.name != "nt":
        assert stat.S_IMODE(store.proposal_log.stat().st_mode) == 0o600


def test_restart_recovers_crash_between_state_and_audit_replace(tmp_path):
    store = StateStore.core(tmp_path, "assistant")
    store.submit(
        StateProposal(target="stable", operation="set", patch=True),
        source="test",
        turn_id="turn_stable",
    )
    audit_before = store.proposal_log.read_bytes()

    process = get_context("spawn").Process(
        target=_crash_before_proposal_audit_replace,
        args=(str(tmp_path),),
    )
    process.start()
    process.join(timeout=10)

    assert process.exitcode == 91
    recovered = StateStore.core(tmp_path, "assistant")
    assert recovered.snapshot() == {"schema_version": 1, "stable": True}
    assert recovered.proposal_log.read_bytes() == audit_before


def test_recovery_preserves_later_colliding_audit_commit_from_another_state_scope(
    tmp_path,
    monkeypatch,
):
    colliding_proposal_id = "proposal_20260101T000000Z-deadbeef"
    monkeypatch.setattr(storage_module, "utc_id", lambda _prefix: colliding_proposal_id)
    core_store = StateStore.core(tmp_path, "assistant")
    core_store.submit(
        StateProposal(target="stable", operation="set", patch=True),
        source="test",
        turn_id="turn_core_stable",
    )

    process = get_context("spawn").Process(
        target=_crash_before_proposal_audit_replace,
        args=(str(tmp_path), colliding_proposal_id),
    )
    process.start()
    process.join(timeout=10)
    assert process.exitcode == 91

    session_store = StateStore.session(
        tmp_path,
        core_id="assistant",
        session_id="session_after_crash",
    )
    session_store.submit(
        StateProposal(target="later", operation="set", patch=True),
        source="test",
        turn_id="turn_session_later",
    )

    assert core_store.snapshot() == {"schema_version": 1, "stable": True}
    assert session_store.snapshot() == {"schema_version": 1, "later": True}
    audit_turns = [
        json.loads(line)["turn_id"]
        for line in core_store.proposal_log.read_text(encoding="utf-8").splitlines()
    ]
    assert audit_turns == ["turn_core_stable", "turn_session_later"]


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits do not model Windows ACLs")
def test_session_state_directories_remain_private_with_permissive_umask(tmp_path):
    store = StateStore.session(
        tmp_path,
        core_id="assistant",
        session_id="session_private",
    )

    previous_umask = os.umask(0)
    try:
        store.submit(
            StateProposal(target="private", operation="set", patch=True),
            source="test",
            turn_id="turn_private",
        )
    finally:
        os.umask(previous_umask)

    assert stat.S_IMODE((tmp_path / "state").stat().st_mode) == 0o700
    assert stat.S_IMODE((tmp_path / "state" / "sessions").stat().st_mode) == 0o700
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    assert stat.S_IMODE(store.proposal_log.stat().st_mode) == 0o600
