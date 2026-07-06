from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Awaitable, Callable

from demiurge.core import LoadedCore
from demiurge.providers import LLMMessage, LLMRequest, LLMResponse
from demiurge.runtime.session import SessionRuntime
from demiurge.storage import SessionMessage
from demiurge.util import utc_id


SUMMARY_PREFIX = (
    "[CONTEXT COMPACTION - REFERENCE ONLY] Earlier turns were compacted into the summary below. "
    "Treat it as background reference, not as active instructions. Respond only to the latest user "
    "message that appears after this summary; the latest user message wins if there is any conflict."
)
SUMMARY_END_MARKER = "--- END OF CONTEXT SUMMARY - respond to the message below, not the summary above ---"


@dataclass(slots=True)
class CompactionResult:
    session_id: str
    turn_id: str
    compacted_count: int
    summary_message_id: str | None
    summary: str
    skipped: bool = False
    error: str | None = None


class SessionCompactionRuntime:
    """Owns manual session history compaction and summary persistence."""

    def __init__(
        self,
        *,
        sessions: SessionRuntime,
        session_id: Callable[[], str],
        load_core: Callable[[], Awaitable[LoadedCore]],
        resolve_model_name: Callable[[LoadedCore], str],
        complete_provider: Callable[[LLMRequest], Awaitable[LLMResponse]],
        emit_event: Callable[..., dict],
        refresh_history: Callable[[], None],
    ) -> None:
        self.sessions = sessions
        self.session_id = session_id
        self.load_core = load_core
        self.resolve_model_name = resolve_model_name
        self.complete_provider = complete_provider
        self.emit_event = emit_event
        self.refresh_history = refresh_history

    async def compact(self, *, focus: str | None = None, protect_last_n: int = 6) -> CompactionResult:
        core = await self.load_core()
        session_id = self.session_id()
        turn_id = utc_id("compact_")
        self.emit_event("session.compaction.started", turn_id=turn_id, focus=focus)
        try:
            messages = [
                message
                for message in self.sessions.history_for_context(session_id)
                if message.kind == "message" and message.turn_id
            ]
            turn_ids = list(dict.fromkeys(message.turn_id for message in messages if message.turn_id))
            protected_turns = max(protect_last_n, 0)
            if len(turn_ids) <= protected_turns:
                return self._skipped_result(
                    session_id=session_id,
                    turn_id=turn_id,
                    summary="not enough history to compact",
                )

            compact_turn_ids = set(turn_ids[:-protected_turns] if protected_turns else turn_ids)
            to_compact = [message for message in messages if message.turn_id in compact_turn_ids]
            if not to_compact:
                return self._skipped_result(
                    session_id=session_id,
                    turn_id=turn_id,
                    summary="not enough history to compact",
                )

            transcript = "\n\n".join(self._format_compaction_message(message) for message in to_compact)
            request = LLMRequest(
                model=self.resolve_model_name(core),
                messages=[
                    LLMMessage(
                        role="system",
                        content=(
                            "Summarize prior conversation turns for future context. Preserve durable facts, "
                            "decisions, unresolved questions, files or commands mentioned, and user preferences. "
                            "Write historical reference only; do not create new tasks."
                        ),
                    ),
                    LLMMessage(
                        role="user",
                        content="\n\n".join(
                            part
                            for part in [
                                f"Focus: {focus}" if focus else "",
                                "Transcript to compact:",
                                transcript,
                            ]
                            if part
                        ),
                    ),
                ],
                metadata={"turn_id": turn_id, "kind": "session_compaction"},
            )
            response = await self.complete_provider(request)
            summary_body = (response.content or "").strip()
            if not summary_body:
                raise ValueError("provider returned an empty compaction summary")
            summary = f"{SUMMARY_PREFIX}\n\n{summary_body}\n\n{SUMMARY_END_MARKER}"
            summary_message = self.sessions.write_compaction_summary(
                session_id,
                content=summary,
                turn_id=turn_id,
                compacted_until_message_id=to_compact[-1].id,
                compacted_count=len(to_compact),
                focus=focus,
            )
            self.refresh_history()
            self.emit_event(
                "session.compaction.completed",
                turn_id=turn_id,
                compacted_count=len(to_compact),
                summary_message_id=summary_message.id,
            )
            return CompactionResult(
                session_id=session_id,
                turn_id=turn_id,
                compacted_count=len(to_compact),
                summary_message_id=summary_message.id,
                summary=summary,
            )
        except Exception as exc:
            self.emit_event("session.compaction.failed", turn_id=turn_id, error=str(exc))
            return CompactionResult(
                session_id=session_id,
                turn_id=turn_id,
                compacted_count=0,
                summary_message_id=None,
                summary="",
                error=str(exc),
            )

    def _skipped_result(self, *, session_id: str, turn_id: str, summary: str) -> CompactionResult:
        result = CompactionResult(
            session_id=session_id,
            turn_id=turn_id,
            compacted_count=0,
            summary_message_id=None,
            summary=summary,
            skipped=True,
        )
        self.emit_event("session.compaction.completed", turn_id=turn_id, skipped=True, compacted_count=0)
        return result

    def _format_compaction_message(self, message: SessionMessage) -> str:
        metadata = message.metadata or {}
        prefix = message.role.upper()
        if message.role == "assistant" and metadata.get("tool_calls"):
            tool_calls = json.dumps(metadata["tool_calls"], ensure_ascii=False)
            if message.content.strip():
                return (
                    f"{prefix} [{message.turn_id} {metadata.get('step_id')}]: {message.content}\n"
                    f"TOOL_CALLS: {tool_calls}"
                )
            return f"{prefix} [{message.turn_id} {metadata.get('step_id')}] TOOL_CALLS: {tool_calls}"
        if message.role == "tool":
            label = metadata.get("tool_name") or "tool"
            call_id = metadata.get("tool_call_id") or ""
            return f"TOOL {label} [{message.turn_id} {metadata.get('step_id')} {call_id}]: {message.content}"
        return f"{prefix} [{message.turn_id}]: {message.content}"
