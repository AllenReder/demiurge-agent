from __future__ import annotations

from .memory_basic.store import load_or_create_session_snapshot, snapshot_blocks


def process(ctx):
    snapshot = load_or_create_session_snapshot(__file__, ctx.input.session_root)
    blocks = snapshot_blocks(snapshot)
    for target in ("memory", "user"):
        block = blocks.get(target)
        if block:
            ctx.input.add("system", block)

