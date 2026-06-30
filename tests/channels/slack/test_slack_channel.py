import hashlib
import hmac
import json

import pytest

from demiurge.channels.slack.bridge import SlackInteractionBridge
from demiurge.core import SlackChannelConfig
from demiurge.runtime.interactions import InteractionDelivery, InteractionItem, InteractionOutbound, InteractionRuntime


class FakeRunner:
    async def run_turn(self, *args, **kwargs):
        raise AssertionError("runner should not be called")


class FakeApi:
    def __init__(self):
        self.sent = []

    def post_message(self, *, channel, text, thread_ts=None):
        self.sent.append({"channel": channel, "text": text, "thread_ts": thread_ts})
        return {"ok": True}


def _bridge(config=None):
    return SlackInteractionBridge(
        api=FakeApi(),
        config=config or SlackChannelConfig(enabled=True, signing_secret="secret", bot_token="token"),
        runtime=InteractionRuntime(FakeRunner()),
    )


def test_slack_verifies_signature(monkeypatch):
    bridge = _bridge()
    raw = b'{"type":"event_callback"}'
    monkeypatch.setattr("demiurge.channels.slack.bridge.time.time", lambda: 1000)
    signature = "v0=" + hmac.new(b"secret", b"v0:1000:" + raw, hashlib.sha256).hexdigest()

    assert bridge._verify_signature({"x-slack-request-timestamp": "1000", "x-slack-signature": signature}, raw)
    assert not bridge._verify_signature({"x-slack-request-timestamp": "1", "x-slack-signature": signature}, raw)


def test_slack_normalizes_event_callback():
    config = SlackChannelConfig(enabled=True, signing_secret="secret", bot_token="token", bot_user_id="Ubot", allowed_channels=["C1"])
    bridge = _bridge(config)

    inbound = bridge.normalize_request(
        {
            "team_id": "T1",
            "event_id": "E1",
            "event": {
                "type": "app_mention",
                "channel": "C1",
                "user": "U1",
                "text": "<@Ubot> hello",
                "ts": "123.4",
            },
        }
    )

    assert inbound is not None
    assert inbound.text == "hello"
    assert inbound.source == "C1"
    assert inbound.reply_to == "123.4"
    assert inbound.conversation_key == "slack:T1:C1"


def test_slack_app_mentions_only_ignores_plain_message_without_mention():
    bridge = _bridge(SlackChannelConfig(enabled=True, signing_secret="secret", bot_token="token", allowed_channels=["C1"]))

    inbound = bridge.normalize_request(
        {
            "team_id": "T1",
            "event": {"type": "message", "channel": "C1", "user": "U1", "text": "hello", "ts": "123.4"},
        }
    )

    assert inbound is None


@pytest.mark.asyncio
async def test_slack_deliver_posts_message():
    bridge = _bridge()

    await bridge.deliver(
        InteractionOutbound(
            "slack",
            items=[InteractionItem.delivery_item(InteractionDelivery(text="hi"))],
            metadata={"source": "C1", "slack_thread_ts": "123.4"},
        )
    )

    assert bridge.api.sent == [{"channel": "C1", "text": "hi", "thread_ts": "123.4"}]


def test_slack_url_verification_request(monkeypatch):
    bridge = _bridge()
    body = {"type": "url_verification", "challenge": "abc"}
    raw = json.dumps(body).encode("utf-8")
    monkeypatch.setattr("demiurge.channels.slack.bridge.time.time", lambda: 1000)
    signature = "v0=" + hmac.new(b"secret", b"v0:1000:" + raw, hashlib.sha256).hexdigest()

    assert bridge._verify_signature({"x-slack-request-timestamp": "1000", "x-slack-signature": signature}, raw)
