from __future__ import annotations

from .context_reseed.store import ContextReseedStore, load_reseed_config


EXPLICIT_TRIGGERS = (
    "reseed",
    "continuity note",
    "handoff note",
    "session note",
    "context note",
)


def process(ctx):
    config = load_reseed_config(__file__)
    store = ContextReseedStore.from_config(config)
    user_text = str(ctx.turn.user_input.content or "")
    if store.mode == "explicit" and not _explicitly_requested(user_text):
        return
    ctx.capability.require("fs.write", slot_path=ctx.slot_path)
    note = store.write_turn_note(
        turn_id=ctx.turn.turn_id,
        user_text=user_text,
        assistant_text=str(ctx.output.response_text or ""),
        history=ctx.history.recent_messages(8, roles=("user", "assistant")),
    )
    if store.notice:
        ctx.output.notice(
            f"Updated context reseed note ({len(note):,} chars).",
            delivery_metadata={"context_reseed": "updated"},
        )


def _explicitly_requested(text: str) -> bool:
    lowered = text.lower()
    return any(trigger in lowered for trigger in EXPLICIT_TRIGGERS)
