import pytest

from demiurge.runtime.conversation_keys import build_conversation_key


def test_build_conversation_key_encodes_ids_and_thread():
    assert (
        build_conversation_key("slack", "channel", "T1", "C:1", thread_id="1712345678.000100")
        == "slack:channel:T1:C%3A1:thread:1712345678.000100"
    )


def test_build_conversation_key_preserves_id_whitespace():
    assert build_conversation_key("webhook", "source", " alice ") == "webhook:source:%20alice%20"


def test_build_conversation_key_requires_base_id():
    with pytest.raises(ValueError, match="at least one id"):
        build_conversation_key("telegram", "dm")

    with pytest.raises(ValueError, match="must not be empty"):
        build_conversation_key("telegram", "dm", "")


def test_build_conversation_key_ignores_empty_thread_id():
    assert build_conversation_key("telegram", "dm", 123, thread_id="") == "telegram:dm:123"


@pytest.mark.parametrize("channel", ["Telegram", "telegram-dm", "", "telegram:dm", " telegram"])
def test_build_conversation_key_rejects_invalid_channel(channel):
    with pytest.raises(ValueError, match="channel"):
        build_conversation_key(channel, "dm", "123")


@pytest.mark.parametrize("scope", ["DM", "private-chat", "", "dm:private", "dm "])
def test_build_conversation_key_rejects_invalid_scope(scope):
    with pytest.raises(ValueError, match="scope"):
        build_conversation_key("telegram", scope, "123")
