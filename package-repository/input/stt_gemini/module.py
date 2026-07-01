from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Mapping

from .stt_gemini.transcriber import audio_attachments, load_transcription_config, transcribe_attachments


async def process(ctx):
    config = load_transcription_config(__file__)
    attachments = tuple(ctx.input.raw_input.attachments or ())
    if not attachments:
        return

    workspace = Path(ctx.input.workspace or ".").resolve()
    session_root = Path(ctx.input.session_root).resolve()
    candidates = audio_attachments(attachments, config, workspace=workspace, session_root=session_root)
    if not candidates:
        return

    ctx.capability.require("network.fetch", slot_path=ctx.slot_path)
    result = await asyncio.to_thread(
        transcribe_attachments,
        attachments,
        config,
        workspace=workspace,
        session_root=session_root,
    )
    if result.text.strip():
        ctx.input.add("user", _render_transcript(result.text, result.metadata, config), history_policy="persist")
        if _config_bool(config.get("activate_skill"), default=True):
            ctx.skills.activate("stt_transcription")


def _render_transcript(text: str, metadata: Mapping[str, Any], config: Mapping[str, Any]) -> str:
    label = str(config.get("transcript_label") or "Voice message transcript").strip()
    provider = str(metadata.get("provider") or config.get("provider") or "stt").strip()
    parts = [f"{label} ({provider}):", text.strip()]
    if _config_bool(config.get("include_metadata"), default=True):
        summary = _metadata_summary(metadata)
        if summary:
            parts.append(f"Transcript metadata: {summary}")
    return "\n".join(parts).strip()


def _metadata_summary(metadata: Mapping[str, Any]) -> str:
    keys = ("model", "language", "confidence", "duration_seconds")
    summary = {key: metadata.get(key) for key in keys if metadata.get(key) is not None}
    attachments = metadata.get("attachments")
    if isinstance(attachments, list) and attachments:
        sources = []
        for item in attachments:
            if not isinstance(item, Mapping):
                continue
            source = item.get("source")
            if isinstance(source, Mapping):
                sources.append({key: source.get(key) for key in ("id", "filename", "media_type") if source.get(key)})
        if sources:
            summary["sources"] = sources
    return json.dumps(summary, ensure_ascii=False, sort_keys=True) if summary else ""


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
