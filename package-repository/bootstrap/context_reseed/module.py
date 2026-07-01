from __future__ import annotations

from .context_reseed.store import ContextReseedStore, load_reseed_config


def process(ctx):
    ctx.capability.require("fs.read", slot_path=ctx.slot_path)
    store = ContextReseedStore.from_config(load_reseed_config(__file__))
    block = store.read_bootstrap_block()
    if block:
        ctx.bootstrap.add(block)
