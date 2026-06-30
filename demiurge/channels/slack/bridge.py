from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from typing import Any

from demiurge.channels.base import TextChannelBridgeBase, resolve_env_value, runtime_factory_for_app
from demiurge.channels.slack.api import SlackApi
from demiurge.channels.webhook_server import WebhookHttpServer
from demiurge.core import SlackChannelConfig
from demiurge.runtime.interactions import InteractionInbound, InteractionRuntime


class SlackInteractionBridge(TextChannelBridgeBase):
    def __init__(
        self,
        *,
        api: SlackApi,
        config: SlackChannelConfig,
        runtime: InteractionRuntime | None = None,
        runtime_factory=None,
        tool_display: str = "summary",
        busy_mode: str = "interrupt",
    ) -> None:
        super().__init__(
            channel_name="slack",
            runtime=runtime,
            runtime_factory=runtime_factory,
            tool_display=tool_display,
            busy_mode=busy_mode,
        )
        self.api = api
        self.config = config
        self.signing_secret = resolve_env_value(config.signing_secret_env, config.signing_secret)
        self.allowed_teams = set(config.allowed_teams)
        self.allowed_channels = set(config.allowed_channels)
        self.allowed_users = set(config.allowed_users)
        self.server = WebhookHttpServer(host=config.host, port=config.port, path=config.path, handler=self._handle_request)

    @classmethod
    def from_config(cls, runtime: InteractionRuntime | None, config: SlackChannelConfig, **kwargs: Any) -> "SlackInteractionBridge":
        token = resolve_env_value(config.bot_token_env, config.bot_token)
        if not token:
            raise RuntimeError("slack channel requires bot_token_env with a value or bot_token")
        if not resolve_env_value(config.signing_secret_env, config.signing_secret):
            raise RuntimeError("slack channel requires signing_secret_env with a value or signing_secret")
        return cls(api=SlackApi(token), config=config, runtime=runtime, **kwargs)

    async def run_forever(self) -> None:
        await self.server.run_forever()

    async def _handle_request(self, request: dict[str, Any]) -> dict[str, Any]:
        headers = request.get("headers") or {}
        raw_body = request.get("raw_body") or b""
        body = request.get("body") or {}
        if not self._verify_signature(headers, raw_body):
            return {"status": 401, "body": {"error": "invalid slack signature"}}
        if body.get("type") == "url_verification":
            return {"status": 200, "body": {"challenge": body.get("challenge")}}
        inbound = self.normalize_request(body)
        if inbound is None:
            return {"status": 200, "body": {"ok": True, "ignored": True}}
        await self.handle_inbound(inbound)
        return {"status": 200, "body": {"ok": True}}

    def normalize_request(self, body: dict[str, Any]) -> InteractionInbound | None:
        if "payload" in body and isinstance(body.get("payload"), str):
            try:
                body = json.loads(str(body["payload"]))
            except json.JSONDecodeError:
                return None
        if "command" in body:
            team_id = str(body.get("team_id") or "")
            channel_id = str(body.get("channel_id") or "")
            user_id = str(body.get("user_id") or "")
            if not self._allowed(team_id=team_id, channel_id=channel_id, user_id=user_id):
                return None
            text = str(body.get("text") or "").strip()
            if not text:
                return None
            return InteractionInbound(
                channel="slack",
                text=text,
                source=channel_id,
                reply_to=str(body.get("thread_ts") or "") or None,
                conversation_key=f"slack:{team_id}:{channel_id}",
                metadata={
                    "slack_team_id": team_id,
                    "slack_channel_id": channel_id,
                    "slack_user_id": user_id,
                    "slack_response_url": body.get("response_url"),
                },
            )
        event = body.get("event") if isinstance(body.get("event"), dict) else body
        if not isinstance(event, dict):
            return None
        if event.get("bot_id") or event.get("subtype") == "bot_message":
            return None
        team_id = str(body.get("team_id") or event.get("team") or "")
        channel_id = str(event.get("channel") or "")
        user_id = str(event.get("user") or "")
        if not self._allowed(team_id=team_id, channel_id=channel_id, user_id=user_id):
            return None
        text = str(event.get("text") or "").strip()
        if not text:
            return None
        event_type = str(event.get("type") or "")
        if self.config.app_mentions_only:
            if event_type != "app_mention":
                return None
            if self.config.bot_user_id:
                text = text.replace(f"<@{self.config.bot_user_id}>", "").strip()
        elif self.config.bot_user_id:
            mention = f"<@{self.config.bot_user_id}>"
            if event_type == "app_mention":
                text = text.replace(mention, "").strip()
        thread_ts = str(event.get("thread_ts") or event.get("ts") or "") or None
        return InteractionInbound(
            channel="slack",
            text=text,
            source=channel_id,
            reply_to=thread_ts,
            conversation_key=f"slack:{team_id}:{channel_id}",
            metadata={
                "slack_team_id": team_id,
                "slack_channel_id": channel_id,
                "slack_user_id": user_id,
                "slack_thread_ts": thread_ts,
                "slack_event_id": body.get("event_id"),
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
        channel = str((metadata or {}).get("slack_channel_id") or source)
        thread_ts = str((metadata or {}).get("slack_thread_ts") or reply_to or "") or None
        return await asyncio.to_thread(self.api.post_message, channel=channel, text=text, thread_ts=thread_ts)

    def _verify_signature(self, headers: dict[str, str], raw_body: bytes) -> bool:
        secret = self.signing_secret
        if not secret:
            return False
        timestamp = str(headers.get("x-slack-request-timestamp") or "")
        signature = str(headers.get("x-slack-signature") or "")
        if not timestamp.isdigit() or not signature.startswith("v0="):
            return False
        if abs(time.time() - int(timestamp)) > 60 * 5:
            return False
        base = b"v0:" + timestamp.encode("utf-8") + b":" + raw_body
        expected = "v0=" + hmac.new(secret.encode("utf-8"), base, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)

    def _allowed(self, *, team_id: str, channel_id: str, user_id: str) -> bool:
        if self.allowed_teams and team_id not in self.allowed_teams:
            return False
        if self.allowed_channels and channel_id not in self.allowed_channels:
            return False
        if self.allowed_users and user_id not in self.allowed_users:
            return False
        return True


def build_slack_gateway_bridge(app: Any, config: SlackChannelConfig) -> SlackInteractionBridge:
    return SlackInteractionBridge.from_config(
        None,
        config,
        runtime_factory=runtime_factory_for_app(app),
        tool_display=getattr(app, "tool_display", "summary"),
        busy_mode=getattr(app, "channel_busy_mode", "interrupt"),
    )


def validate_slack_schedule_target(config: SlackChannelConfig, delivery: Any) -> None:
    target = delivery.delivery_target
    if not target:
        raise RuntimeError("slack schedule delivery requires target")
    if config.allowed_channels and target not in set(config.allowed_channels):
        raise RuntimeError("slack schedule delivery target is not allowed by allowed_channels")
