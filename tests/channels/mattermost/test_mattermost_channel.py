import pytest

from demiurge.channels.mattermost.bridge import MattermostInteractionBridge
from demiurge.core import MattermostChannelConfig
from demiurge.runtime.interactions import InteractionDelivery, InteractionItem, InteractionOutbound, InteractionRuntime


class FakeRunner:
    async def run_turn(self, *args, **kwargs):
        raise AssertionError("runner should not be called")


class FakeApi:
    def __init__(self):
        self.sent = []

    def post_message(self, *, channel_id, text, root_id=None):
        self.sent.append({"channel_id": channel_id, "text": text, "root_id": root_id})
        return {"id": "post"}


def _bridge(config=None):
    return MattermostInteractionBridge(
        api=FakeApi(),
        config=config or MattermostChannelConfig(enabled=True, base_url="https://mattermost.example", token="token", webhook_token="secret"),
        runtime=InteractionRuntime(FakeRunner()),
    )


def test_mattermost_normalizes_webhook_payload_and_strips_trigger_word():
    bridge = _bridge(MattermostChannelConfig(enabled=True, base_url="https://mattermost.example", token="token", webhook_token="secret", allowed_channels=["C1"]))

    inbound = bridge.normalize_request({"token": "secret", "channel_id": "C1", "user_id": "U1", "trigger_word": "demiurge", "text": "demiurge hello"})

    assert inbound is not None
    assert inbound.channel == "mattermost"
    assert inbound.text == "hello"
    assert inbound.source == "C1"
    assert inbound.conversation_key == "mattermost:channel:C1"


def test_mattermost_thread_root_uses_thread_conversation_key():
    bridge = _bridge(
        MattermostChannelConfig(
            enabled=True,
            base_url="https://mattermost.example",
            token="token",
            webhook_token="secret",
            allowed_channels=["C1"],
        )
    )

    inbound = bridge.normalize_request(
        {
            "token": "secret",
            "channel_id": "C1",
            "user_id": "U1",
            "root_id": "root:1",
            "post_id": "post:1",
            "text": "hello",
        }
    )

    assert inbound is not None
    assert inbound.reply_to == "root:1"
    assert inbound.conversation_key == "mattermost:channel:C1:thread:root%3A1"
    assert inbound.metadata["mattermost_root_id"] == "root:1"


def test_mattermost_post_id_does_not_create_thread_conversation_key():
    bridge = _bridge(
        MattermostChannelConfig(
            enabled=True,
            base_url="https://mattermost.example",
            token="token",
            webhook_token="secret",
            allowed_channels=["C1"],
        )
    )

    inbound = bridge.normalize_request(
        {
            "token": "secret",
            "channel_id": "C1",
            "user_id": "U1",
            "post_id": "post:1",
            "text": "hello",
        }
    )

    assert inbound is not None
    assert inbound.reply_to == "post:1"
    assert inbound.conversation_key == "mattermost:channel:C1"
    assert inbound.metadata["mattermost_root_id"] is None


def test_mattermost_rejects_unauthorized_request():
    bridge = _bridge()

    assert not bridge._authorized({"token": "bad"}, {})
    assert bridge._authorized({"token": "secret"}, {})


@pytest.mark.asyncio
async def test_mattermost_deliver_posts_message():
    bridge = _bridge()

    await bridge.deliver(
        InteractionOutbound(
            "mattermost",
            session_id="session_1",
            items=[InteractionItem.delivery_item(InteractionDelivery(text="hi"))],
            metadata={"source": "C1", "mattermost_root_id": "root"},
        )
    )

    assert bridge.api.sent == [{"channel_id": "C1", "text": "hi", "root_id": "root"}]
