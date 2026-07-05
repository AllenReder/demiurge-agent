from __future__ import annotations

from .self_learning_skills.review import (
    SELF_LEARNING_STATE_KEY,
    SKILL_REVIEW_TOOLS,
    build_review_context,
    load_self_learning_config,
    summarize_review_result,
)


async def process(ctx):
    config = load_self_learning_config(__file__)
    count = _state_counter(ctx) + 1
    if count < config.interval:
        ctx.state.session.set(SELF_LEARNING_STATE_KEY, count)
        return

    ctx.state.session.set(SELF_LEARNING_STATE_KEY, 0)
    history = ctx.history.recent_messages(config.history_limit)
    try:
        result = await ctx.agents.run(
            ctx.turn.core_id,
            "Review the supplied recent conversation and update durable skills only if warranted.",
            context=build_review_context(
                config,
                history=history,
                current_response=ctx.output.response_text,
                turn_id=ctx.turn.turn_id,
            ),
            input_slots=["base_input"],
            output_slots=["base_output"],
            tools=list(SKILL_REVIEW_TOOLS),
            use_bootstrap=True,
        )
    except Exception as exc:
        if config.notify:
            ctx.output.notice(
                f"Self-learning skill review failed: {exc}",
                delivery_metadata={"self_learning_skills": "failed"},
            )
        return

    notice = summarize_review_result(result)
    if config.notify and notice:
        ctx.output.notice(
            notice,
            delivery_metadata={"self_learning_skills": "completed"},
        )


def _state_counter(ctx) -> int:
    value = ctx.state.session.get(SELF_LEARNING_STATE_KEY, 0)
    if isinstance(value, bool):
        return 0
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return parsed if parsed >= 0 else 0
