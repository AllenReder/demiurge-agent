from __future__ import annotations

import asyncio
import email.utils
from typing import Any

from demiurge.channels.base import TextChannelBridgeBase, resolve_env_value
from demiurge.channels.email.client import EmailClient, EmailInboundMessage
from demiurge.core import EmailChannelConfig
from demiurge.runtime.interaction_factory import runtime_factory_for_app
from demiurge.runtime.interactions import InteractionInbound, InteractionRuntime


class EmailInteractionBridge(TextChannelBridgeBase):
    def __init__(
        self,
        *,
        client: EmailClient,
        config: EmailChannelConfig,
        runtime: InteractionRuntime | None = None,
        runtime_factory=None,
        tool_display: str = "summary",
        busy_mode: str = "interrupt",
    ) -> None:
        super().__init__(
            channel_name="email",
            runtime=runtime,
            runtime_factory=runtime_factory,
            tool_display=tool_display,
            busy_mode=busy_mode,
        )
        self.client = client
        self.config = config
        self.allowed_senders = {_normalize_address(sender) for sender in config.allowed_senders}
        self.allowed_recipients = {_normalize_address(recipient) for recipient in config.allowed_recipients}
        if self.allowed_senders and not config.trust_from_headers:
            raise RuntimeError("email allowed_senders requires trust_from_headers: true because RFC5322 From headers are spoofable")

    @classmethod
    def from_config(cls, runtime: InteractionRuntime | None, config: EmailChannelConfig, **kwargs: Any) -> "EmailInteractionBridge":
        smtp_username = resolve_env_value(config.smtp_username_env, config.smtp_username)
        smtp_password = resolve_env_value(config.smtp_password_env, config.smtp_password)
        imap_username = resolve_env_value(config.imap_username_env, config.imap_username)
        imap_password = resolve_env_value(config.imap_password_env, config.imap_password)
        if not config.smtp_host or not config.imap_host:
            raise RuntimeError("email channel requires smtp_host and imap_host")
        if not smtp_username or not smtp_password:
            raise RuntimeError("email channel requires SMTP username/password env values or inline credentials")
        if not imap_username or not imap_password:
            raise RuntimeError("email channel requires IMAP username/password env values or inline credentials")
        client = EmailClient(
            smtp_host=config.smtp_host,
            smtp_port=config.smtp_port,
            smtp_username=smtp_username,
            smtp_password=smtp_password,
            smtp_starttls=config.smtp_starttls,
            imap_host=config.imap_host,
            imap_port=config.imap_port,
            imap_username=imap_username,
            imap_password=imap_password,
            mailbox=config.mailbox,
            from_address=config.from_address,
        )
        return cls(client=client, config=config, runtime=runtime, **kwargs)

    async def run_forever(self) -> None:
        while True:
            messages = await asyncio.to_thread(self.client.poll_unseen)
            for message in messages:
                inbound = self.normalize_message(message)
                if inbound is not None:
                    await self.handle_inbound(inbound)
            await asyncio.sleep(self.config.poll_interval)

    def normalize_message(self, message: EmailInboundMessage) -> InteractionInbound | None:
        sender = _normalize_address(message.sender)
        if self.allowed_senders and sender not in self.allowed_senders:
            return None
        text = message.body.strip()
        if not text:
            return None
        conversation_key = f"email:{sender}"
        return InteractionInbound(
            channel="email",
            text=text,
            source=sender,
            reply_to=message.message_id or message.uid,
            conversation_key=conversation_key,
            metadata={
                "email_sender": sender,
                "email_subject": message.subject,
                "email_message_id": message.message_id,
                "email_references": message.references,
            },
        )

    async def _send_text(
        self,
        source: str,
        text: str,
        *,
        reply_to: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Any:
        recipient = _normalize_address(str((metadata or {}).get("email_sender") or source))
        if self.allowed_recipients and recipient not in self.allowed_recipients:
            raise RuntimeError("email delivery recipient is not allowed")
        subject = str((metadata or {}).get("email_subject") or "Demiurge response")
        if not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"
        references = str((metadata or {}).get("email_references") or "").split()
        message_id = str((metadata or {}).get("email_message_id") or reply_to or "") or None
        await asyncio.to_thread(
            self.client.send_text,
            to_address=recipient,
            subject=subject,
            body=text,
            in_reply_to=message_id,
            references=references,
        )
        return None


def build_email_gateway_bridge(app: Any, config: EmailChannelConfig) -> EmailInteractionBridge:
    return EmailInteractionBridge.from_config(
        None,
        config,
        runtime_factory=runtime_factory_for_app(app),
        tool_display=getattr(app, "tool_display", "summary"),
        busy_mode=getattr(app, "channel_busy_mode", "interrupt"),
    )


def validate_email_schedule_target(config: EmailChannelConfig, delivery: Any) -> None:
    target = delivery.delivery_target
    if not target:
        raise RuntimeError("email schedule delivery requires target")
    normalized = _normalize_address(str(target))
    if config.allowed_recipients and normalized not in {_normalize_address(item) for item in config.allowed_recipients}:
        raise RuntimeError("email schedule delivery target is not allowed by allowed_recipients")


def _normalize_address(value: str) -> str:
    _, address = email.utils.parseaddr(value)
    return (address or value).strip().lower()
