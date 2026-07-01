from __future__ import annotations

import asyncio
from pathlib import Path

from demiurge.sdk import ToolResult

from .tts_openai.synthesizer import load_synthesis_config, synthesize_to_file


async def execute(ctx, args):
    ctx.capability.require("network.fetch", slot_path=ctx.slot_path)
    text = str(args.get("text") or "").strip()
    if not text:
        return ToolResult(content="text is required", is_error=True)
    config = load_synthesis_config(__file__)
    synthesis = await asyncio.to_thread(
        synthesize_to_file,
        text,
        config,
        workspace=Path(ctx.workspace or ".").resolve(),
        turn_id=ctx.turn.turn_id,
    )
    data = {
        "path": str(synthesis.path),
        "media_type": synthesis.media_type,
        "metadata": synthesis.metadata,
    }
    ctx.output.send_audio(
        synthesis.path,
        media_type=synthesis.media_type,
        artifact_metadata=synthesis.metadata,
        history_policy="transient",
    )
    return ToolResult(
        content="sent audio",
        data=data,
        model_output="Sent speech audio to the user.",
        display_output="sent audio",
    )
