from demiurge.runtime.prompts import (
    PromptChoiceRuntime,
    PromptDeliveryRuntime,
    choice_button_rows,
    choice_callback_data,
    format_prompt_text,
    normalize_prompt_answer,
    parse_choice_callback_data,
)
from demiurge.runtime.interactions import InteractionInbound, UserPromptRequest


def test_normalize_prompt_answer_maps_number_to_choice():
    result = normalize_prompt_answer("2", ["fast", "careful"])

    assert result.text == "careful"
    assert result.matched_choice is True
    assert result.index == 1


def test_normalize_prompt_answer_can_default_empty_to_first_choice():
    result = normalize_prompt_answer("", ["first", "second"], empty="first")

    assert result.text == "first"
    assert result.matched_choice is True
    assert result.index == 0


def test_normalize_prompt_answer_keeps_invalid_number_as_text():
    result = normalize_prompt_answer("9", ["fast", "careful"])

    assert result.text == "9"
    assert result.matched_choice is False
    assert result.index is None


def test_format_prompt_text_renders_numbered_choices():
    assert format_prompt_text("Which path?", ["fast", "careful"]) == "Which path?\n1. fast\n2. careful"


def test_choice_callback_helpers_round_trip_index():
    assert choice_callback_data(3) == "choice:3"
    assert parse_choice_callback_data("choice:3") == 3
    assert parse_choice_callback_data("approval:3") is None
    assert parse_choice_callback_data("choice:nope") is None


def test_choice_button_rows_shorten_labels_and_use_callback_data():
    rows = choice_button_rows(["short", "x" * 40], label_limit=12)

    assert rows == [
        [{"text": "1. short", "callback_data": "choice:0"}],
        [{"text": "2. xxxxxxxxx...", "callback_data": "choice:1"}],
    ]


def test_prompt_choice_runtime_consumes_numbered_text_once():
    runtime = PromptChoiceRuntime()
    runtime.remember("conversation", ["fast", "careful"])

    result = runtime.consume_text("conversation", "2")

    assert result.text == "careful"
    assert result.consumed is True
    assert result.matched_choice is True
    assert result.index == 1
    assert runtime.consume_text("conversation", "1").text == "1"


def test_prompt_choice_runtime_consumes_invalid_text_and_clears_pending_choice():
    runtime = PromptChoiceRuntime()
    runtime.remember("conversation", ["fast", "careful"])

    result = runtime.consume_text("conversation", "other")

    assert result.text == "other"
    assert result.consumed is True
    assert result.matched_choice is False
    assert runtime.get("conversation") is None


def test_prompt_choice_runtime_remember_without_choices_clears_pending_choice():
    runtime = PromptChoiceRuntime()
    runtime.remember("conversation", ["fast", "careful"])

    runtime.remember("conversation", [])

    assert runtime.get("conversation") is None


def test_prompt_choice_runtime_consumes_callback_data():
    runtime = PromptChoiceRuntime()
    runtime.remember("conversation", ["fast", "careful"])

    result = runtime.consume_callback_data("conversation", "choice:1")

    assert result is not None
    assert result.text == "careful"
    assert result.consumed is True
    assert result.matched_choice is True
    assert result.index == 1
    assert runtime.get("conversation") is None


def test_prompt_choice_runtime_keeps_choices_for_invalid_callback_data():
    runtime = PromptChoiceRuntime()
    runtime.remember("conversation", ["fast", "careful"])

    assert runtime.consume_callback_data("conversation", "choice:nope") is None
    assert runtime.get("conversation") == ["fast", "careful"]
    assert runtime.consume_callback_data("conversation", "choice:9") is None
    assert runtime.get("conversation") == ["fast", "careful"]


def test_prompt_delivery_runtime_prepares_delivery_and_remembers_choices():
    runtime = PromptDeliveryRuntime()

    delivery = runtime.prepare(
        UserPromptRequest(
            question="Which path?",
            choices=["fast", "careful"],
            conversation_key="conversation",
            metadata={"source": 123, "reply_to": 456, "channel": "telegram"},
        )
    )

    assert delivery is not None
    assert delivery.source == "123"
    assert delivery.reply_to == "456"
    assert delivery.text == "Which path?\n1. fast\n2. careful"
    assert delivery.metadata == {"source": 123, "reply_to": 456, "channel": "telegram"}
    assert delivery.choices == ("fast", "careful")
    assert runtime.pending_choices("conversation") == ["fast", "careful"]


def test_prompt_delivery_runtime_returns_none_without_source_but_keeps_choice_state():
    runtime = PromptDeliveryRuntime()

    delivery = runtime.prepare(
        UserPromptRequest(
            question="Which path?",
            choices=["fast", "careful"],
            conversation_key="conversation",
        )
    )

    assert delivery is None
    assert runtime.pending_choices("conversation") == ["fast", "careful"]


def test_prompt_delivery_runtime_resolves_inbound_choice_and_preserves_shape():
    runtime = PromptDeliveryRuntime()
    runtime.prepare(
        UserPromptRequest(
            question="Which path?",
            choices=["fast", "careful"],
            conversation_key="conversation",
            metadata={"source": "chat"},
        )
    )
    inbound = InteractionInbound(
        channel="telegram",
        text="2",
        source="chat",
        reply_to="msg",
        conversation_key="conversation",
        metadata={"telegram_chat_id": 123},
        attachments=["attachment"],
    )

    resolved = runtime.resolve_inbound(inbound)

    assert resolved.text == "careful"
    assert resolved.channel == inbound.channel
    assert resolved.source == inbound.source
    assert resolved.reply_to == inbound.reply_to
    assert resolved.conversation_key == inbound.conversation_key
    assert resolved.metadata == inbound.metadata
    assert resolved.attachments == inbound.attachments
    assert runtime.pending_choices("conversation") is None


def test_prompt_delivery_runtime_consumes_callback_data():
    runtime = PromptDeliveryRuntime()
    runtime.prepare(
        UserPromptRequest(
            question="Which path?",
            choices=["fast", "careful"],
            conversation_key="conversation",
            metadata={"source": "chat"},
        )
    )

    result = runtime.consume_callback_data("conversation", "choice:1")

    assert result is not None
    assert result.text == "careful"
    assert result.index == 1
    assert runtime.pending_choices("conversation") is None
