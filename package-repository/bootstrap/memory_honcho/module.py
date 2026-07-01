from __future__ import annotations

from .memory_honcho.config import load_config
from .memory_honcho.runtime import bootstrap_context


def process(ctx):
    ctx.capability.require("fs.read", slot_path=ctx.slot_path)
    config = load_config(__file__)
    for fragment in bootstrap_context(ctx, config):
        ctx.bootstrap.add(fragment)
