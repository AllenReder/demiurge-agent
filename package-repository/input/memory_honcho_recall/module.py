from __future__ import annotations

from .memory_honcho.config import load_config
from .memory_honcho.runtime import recall


def process(ctx):
    config = load_config(__file__)
    if str(config.get("recall_mode") or "hybrid") == "tools":
        return
    ctx.capability.require("fs.read", slot_path=ctx.slot_path)
    ctx.capability.require("fs.write", slot_path=ctx.slot_path)
    ctx.capability.require("network.fetch", slot_path=ctx.slot_path)
    block = recall(str(ctx.input.raw_input.text or ""), ctx, config)
    if block:
        ctx.input.add("system", block, history_policy="transient")
