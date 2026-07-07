import pytest

from demiurge.channels.email.bridge import EmailInteractionBridge
from demiurge.channels.email.client import EmailInboundMessage
from demiurge.core import EmailChannelConfig
from demiurge.runtime.interactions import InteractionDelivery, InteractionItem, InteractionOutbound, InteractionRuntime


class FakeRunner:
    async def run_turn(self, *args, **kwargs):
        raise AssertionError("runner should not be called")


class FakeClient:
    def __init__(self):
        self.sent = []

    def send_text(self, **kwargs):
        self.sent.append(kwargs)


def _bridge(config=None):
    return EmailInteractionBridge(
        client=FakeClient(),
        config=config or EmailChannelConfig(enabled=True, smtp_host="smtp.example", imap_host="imap.example", smtp_username="u", smtp_password="p", imap_username="u", imap_password="p"),
        runtime=InteractionRuntime(FakeRunner()),
    )


def test_email_normalizes_plain_message_sender():
    bridge = _bridge(EmailChannelConfig(enabled=True, smtp_host="smtp.example", imap_host="imap.example", smtp_username="u", smtp_password="p", imap_username="u", imap_password="p", allowed_senders=["alice@example.com"], trust_from_headers=True))

    inbound = bridge.normalize_message(
        EmailInboundMessage(
            uid="1",
            sender="Alice <alice@example.com>",
            subject="Question",
            body="hello",
            message_id="<m1>",
            references="<root>",
        )
    )

    assert inbound is not None
    assert inbound.channel == "email"
    assert inbound.text == "hello"
    assert inbound.source == "alice@example.com"
    assert inbound.reply_to == "<m1>"
    assert inbound.conversation_key == "email:sender:alice%40example.com"


def test_email_rejects_disallowed_sender():
    bridge = _bridge(EmailChannelConfig(enabled=True, smtp_host="smtp.example", imap_host="imap.example", smtp_username="u", smtp_password="p", imap_username="u", imap_password="p", allowed_senders=["alice@example.com"], trust_from_headers=True))

    inbound = bridge.normalize_message(EmailInboundMessage(uid="1", sender="mallory@example.com", subject="x", body="hello"))

    assert inbound is None


@pytest.mark.asyncio
async def test_email_deliver_sends_threaded_reply():
    bridge = _bridge()

    await bridge.deliver(
        InteractionOutbound(
            "email",
            session_id="session_1",
            items=[InteractionItem.delivery_item(InteractionDelivery(text="hi"))],
            metadata={"source": "alice@example.com", "email_subject": "Question", "email_message_id": "<m1>", "email_references": "<root>"},
        )
    )

    assert bridge.client.sent == [
        {
            "to_address": "alice@example.com",
            "subject": "Re: Question",
            "body": "hi",
            "in_reply_to": "<m1>",
            "references": ["<root>"],
        }
    ]


def test_email_config_requires_credentials(monkeypatch):
    monkeypatch.delenv("DEMIURGE_SMTP_USERNAME", raising=False)
    monkeypatch.delenv("DEMIURGE_SMTP_PASSWORD", raising=False)
    monkeypatch.delenv("DEMIURGE_IMAP_USERNAME", raising=False)
    monkeypatch.delenv("DEMIURGE_IMAP_PASSWORD", raising=False)
    config = EmailChannelConfig(enabled=True, smtp_host="smtp.example", imap_host="imap.example")

    with pytest.raises(RuntimeError, match="SMTP username/password"):
        EmailInteractionBridge.from_config(InteractionRuntime(FakeRunner()), config)


def test_email_allowed_senders_requires_explicit_trust_from_headers():
    config = EmailChannelConfig(
        enabled=True,
        smtp_host="smtp.example",
        imap_host="imap.example",
        smtp_username="u",
        smtp_password="p",
        imap_username="u",
        imap_password="p",
        allowed_senders=["alice@example.com"],
    )

    with pytest.raises(RuntimeError, match="trust_from_headers"):
        _bridge(config)
