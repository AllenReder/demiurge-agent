from __future__ import annotations

from .memory_basic.store import load_or_create_session_snapshot, snapshot_blocks


MEMORY_GUIDANCE = (
    "You have persistent memory across sessions. Use the memory tool to save "
    "compact durable facts: user preferences, corrections, stable profile "
    "details, environment facts, project conventions, and tool or workflow "
    "lessons. Do not save task progress, temporary todos, completed-work logs, "
    "raw data dumps, or reusable procedures that belong in skills. Current "
    "memory is a frozen session snapshot; writes affect future sessions."
)


def process(ctx):
    ctx.input.add("system", MEMORY_GUIDANCE)
    snapshot = load_or_create_session_snapshot(__file__, ctx.input.session_root)
    blocks = snapshot_blocks(snapshot)
    for target in ("memory", "user"):
        block = blocks.get(target)
        if block:
            ctx.input.add("system", block)
