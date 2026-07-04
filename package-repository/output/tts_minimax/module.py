from __future__ import annotations

import asyncio
import re
from typing import Any

from .tts_minimax.synthesizer import load_synthesis_config, synthesize_to_file


_MD_CODE_BLOCK = re.compile(r"```.*?```", re.DOTALL)
_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_MD_URL = re.compile(r"https?://\S+")
_MD_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_MD_ITALIC = re.compile(r"(?<!\*)\*([^*]+)\*(?!\*)")
_MD_INLINE_CODE = re.compile(r"`([^`]+)`")
_MD_HEADER = re.compile(r"^\s{0,3}#{1,6}\s+", re.MULTILINE)
_MD_LIST_ITEM = re.compile(r"^\s*[-*+]\s+", re.MULTILINE)
_MD_HR = re.compile(r"---+")
_MD_EXCESS_NL = re.compile(r"\n{3,}")


async def process(ctx):
    config = load_synthesis_config(__file__)
    text = str(ctx.output.response_text or "").strip()
    summarizer_core = config.get("summarizer_core")
    if summarizer_core:
        summary = await ctx.agents.run(
            str(summarizer_core),
            text,
            output_slots=["result_output"],
        )
        result = summary.result
        if isinstance(result, dict) and result.get("text"):
            text = str(result["text"]).strip()
        elif summary.content:
            text = str(summary.content).strip()
    if not text:
        return

    if _config_bool(config.get("strip_markdown"), default=True):
        text = _strip_markdown_for_tts(text)
    max_text_length = _positive_int(config.get("max_text_length"), default=10000)
    if len(text) > max_text_length:
        text = text[:max_text_length]
    if not text:
        return

    synthesis = await asyncio.to_thread(
        synthesize_to_file,
        text,
        config,
        workspace=ctx.output.workspace,
        turn_id=ctx.turn.turn_id,
    )
    media_type = str(config.get("media_type") or synthesis.media_type)
    caption = config.get("caption")
    caption = str(caption).strip() if caption is not None else None
    summary = config.get("summary")
    summary = str(summary).strip() if summary is not None else None
    ctx.output.send_audio(
        synthesis.path,
        caption=caption or None,
        media_type=media_type,
        summary=summary or None,
        artifact_metadata=synthesis.metadata,
        write_history=False,
    )


def _strip_markdown_for_tts(text: str) -> str:
    text = _MD_CODE_BLOCK.sub(" ", text)
    text = _MD_LINK.sub(r"\1", text)
    text = _MD_URL.sub("", text)
    text = _MD_BOLD.sub(r"\1", text)
    text = _MD_ITALIC.sub(r"\1", text)
    text = _MD_INLINE_CODE.sub(r"\1", text)
    text = _MD_HEADER.sub("", text)
    text = _MD_LIST_ITEM.sub("", text)
    text = _MD_HR.sub("", text)
    text = _MD_EXCESS_NL.sub("\n\n", text)
    return text.strip()


def _config_bool(value: Any, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on", "enabled"}:
            return True
        if normalized in {"0", "false", "no", "off", "disabled"}:
            return False
    return default


def _positive_int(value: Any, *, default: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default
