from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import yaml

from demiurge.sdk import ToolResult

from .tts_minimax.synthesizer import synthesize_to_file


def _load_config() -> dict[str, Any]:
    path = Path(__file__).with_name("config.yaml")
    if not path.exists():
        return {}
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return loaded if isinstance(loaded, dict) else {}


async def execute(ctx, args):
    ctx.capability.require("network.fetch", slot_path=ctx.slot_path)
    text = str(args.get("text") or "").strip()
    if not text:
        return ToolResult(content="text is required", is_error=True)
    config = _load_config()
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
