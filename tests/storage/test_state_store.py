from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from demiurge.storage import StateProposal, StateStore


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
        return store.submit(
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
