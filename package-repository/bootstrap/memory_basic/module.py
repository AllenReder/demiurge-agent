from __future__ import annotations

from .memory_basic.store import MemoryStore, load_memory_config


MEMORY_GUIDANCE = (
    "You have persistent memory across sessions. Use the memory tool to save "
    "compact durable facts: user preferences, corrections, stable profile "
    "details, environment facts, project conventions, and tool or workflow "
    "lessons. Do not save task progress, temporary todos, completed-work logs, "
    "raw data dumps, or reusable procedures that belong in skills. Current "
    "memory is frozen into the session bootstrap context; writes affect future "
    "sessions."
)


def process(ctx):
    config = load_memory_config(__file__)
    store = MemoryStore.from_config(config)
    ctx.bootstrap.add(MEMORY_GUIDANCE)
    blocks = store.context_blocks()
    for target in ("memory", "user"):
        block = blocks.get(target)
        if block:
            ctx.bootstrap.add(block)
