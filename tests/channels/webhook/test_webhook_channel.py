import pytest

from demiurge.channels.webhook import WebhookInteractionBridge
from demiurge.core import WebhookChannelConfig
from demiurge.runtime.interactions import InteractionDelivery, InteractionItem, InteractionOutbound, InteractionRuntime


class FakeRunner:
    async def run_turn(self, *args, **kwargs):
        raise AssertionError("runner should not be called")


@pytest.mark.asyncio
async def test_webhook_requires_token_unless_explicitly_unauthenticated(monkeypatch):
    monkeypatch.delenv("DEMIURGE_WEBHOOK_TOKEN", raising=False)
    config = WebhookChannelConfig(enabled=True)

    with pytest.raises(RuntimeError, match="allow_unauthenticated"):
        WebhookInteractionBridge.from_config(InteractionRuntime(FakeRunner()), config)


def test_webhook_normalizes_json_request_with_callback_allowed_private():
    config = WebhookChannelConfig(enabled=True, allow_unauthenticated=True, allow_private_callback_urls=True)
    bridge = WebhookInteractionBridge.from_config(InteractionRuntime(FakeRunner()), config)

    inbound = bridge.normalize_request(
        {
            "text": "hello",
            "source": "alice",
            "request_id": "r1",
            "callback_url": "http://127.0.0.1:9999/callback",
            "metadata": {"k": "v"},
        },
        headers={"x-test": "1"},
        client="127.0.0.1",
    )

    assert inbound is not None
    assert inbound.channel == "webhook"
    assert inbound.text == "hello"
    assert inbound.source == "alice"
    assert inbound.reply_to == "r1"
    assert inbound.conversation_key == "webhook:alice"
    assert inbound.metadata["webhook_callback_url"] == "http://127.0.0.1:9999/callback"
    assert inbound.metadata["k"] == "v"


@pytest.mark.asyncio
async def test_webhook_deliver_posts_to_callback(monkeypatch):
    calls = []

    def fake_json_request(url, *, payload, allow_private=False, **kwargs):
        calls.append((url, payload, allow_private))
        return {"ok": True}

    monkeypatch.setattr("demiurge.channels.webhook.bridge.json_request", fake_json_request)
    config = WebhookChannelConfig(enabled=True, allow_unauthenticated=True, allow_private_callback_urls=True)
    bridge = WebhookInteractionBridge.from_config(InteractionRuntime(FakeRunner()), config)

    await bridge.deliver(
        InteractionOutbound(
            "webhook",
            session_id="session_1",
            items=[InteractionItem.delivery_item(InteractionDelivery(text="hi"))],
            metadata={"source": "alice", "webhook_callback_url": "http://127.0.0.1/cb"},
        )
    )

    assert calls == [("http://127.0.0.1/cb", {"channel": "webhook", "source": "alice", "reply_to": None, "text": "hi"}, True)]
