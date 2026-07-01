from __future__ import annotations

from .memory_honcho.config import load_config
from .memory_honcho.runtime import sync_turn


def process(ctx):
    config = load_config(__file__)
    ctx.capability.require("fs.read", slot_path=ctx.slot_path)
    ctx.capability.require("fs.write", slot_path=ctx.slot_path)
    ctx.capability.require("network.fetch", slot_path=ctx.slot_path)
    sync_turn(ctx, config)
