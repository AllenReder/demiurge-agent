from __future__ import annotations

import asyncio
from typing import Any

from demiurge.channels.base import TextChannelBridgeBase, resolve_env_value
from demiurge.channels.mattermost.api import MattermostApi
from demiurge.channels.webhook_server import WebhookHttpServer
from demiurge.core import MattermostChannelConfig
from demiurge.runtime.interaction_factory import runtime_factory_for_app
from demiurge.runtime.interactions import InteractionInbound, InteractionRuntime


class MattermostInteractionBridge(TextChannelBridgeBase):
    def __init__(
        self,
        *,
        api: MattermostApi,
        config: MattermostChannelConfig,
        runtime: InteractionRuntime | None = None,
        runtime_factory=None,
        tool_display: str = "summary",
        busy_mode: str = "interrupt",
    ) -> None:
        super().__init__(
            channel_name="mattermost",
            runtime=runtime,
            runtime_factory=runtime_factory,
            tool_display=tool_display,
            busy_mode=busy_mode,
        )
        self.api = api
        self.config = config
        self.webhook_token = resolve_env_value(config.webhook_token_env, config.webhook_token)
        self.allowed_channels = set(config.allowed_channels)
        self.allowed_users = set(config.allowed_users)
        self.server = WebhookHttpServer(host=config.host, port=config.port, path=config.path, handler=self._handle_request)

    @classmethod
    def from_config(cls, runtime: InteractionRuntime | None, config: MattermostChannelConfig, **kwargs: Any) -> "MattermostInteractionBridge":
        token = resolve_env_value(config.token_env, config.token)
        incoming_webhook_url = resolve_env_value(config.incoming_webhook_url_env, config.incoming_webhook_url)
        if not ((config.base_url and token) or incoming_webhook_url):
            raise RuntimeError("mattermost channel requires base_url+token or incoming_webhook_url")
        if not resolve_env_value(config.webhook_token_env, config.webhook_token):
            raise RuntimeError("mattermost channel requires webhook_token_env with a value or webhook_token")
        api = MattermostApi(base_url=config.base_url, token=token, incoming_webhook_url=incoming_webhook_url)
        return cls(api=api, config=config, runtime=runtime, **kwargs)

    async def run_forever(self) -> None:
        await self.server.run_forever()

    async def _handle_request(self, request: dict[str, Any]) -> dict[str, Any]:
        body = request.get("body") or {}
        headers = request.get("headers") or {}
        if not self._authorized(body, headers):
            return {"status": 401, "body": {"error": "unauthorized"}}
        inbound = self.normalize_request(body)
        if inbound is None:
            return {"status": 200, "body": {"ok": True, "ignored": True}}
        await self.handle_inbound(inbound)
        return {"status": 200, "body": {"ok": True}}

    def normalize_request(self, body: dict[str, Any]) -> InteractionInbound | None:
        channel_id = str(body.get("channel_id") or body.get("channel") or "")
        user_id = str(body.get("user_id") or body.get("user_name") or "")
        if self.allowed_channels and channel_id not in self.allowed_channels:
            return None
        if self.allowed_users and user_id not in self.allowed_users:
            return None
        text = str(body.get("text") or body.get("message") or "").strip()
        trigger_word = str(body.get("trigger_word") or "").strip()
        if trigger_word and text.startswith(trigger_word):
            text = text[len(trigger_word):].strip()
        if not text:
            return None
        root_id = str(body.get("root_id") or body.get("post_id") or "") or None
        return InteractionInbound(
            channel="mattermost",
            text=text,
            source=channel_id,
            reply_to=root_id,
            conversation_key=f"mattermost:{channel_id}",
            metadata={
                "mattermost_channel_id": channel_id,
                "mattermost_user_id": user_id,
                "mattermost_root_id": root_id,
                "mattermost_team_id": body.get("team_id"),
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
        channel_id = str((metadata or {}).get("mattermost_channel_id") or source)
        root_id = str((metadata or {}).get("mattermost_root_id") or reply_to or "") or None
        return await asyncio.to_thread(self.api.post_message, channel_id=channel_id, text=text, root_id=root_id)

    def _authorized(self, body: dict[str, Any], headers: dict[str, str]) -> bool:
        token = self.webhook_token
        if not token:
            return False
        return token in {
            str(body.get("token") or ""),
            str(headers.get("x-demiurge-token") or ""),
            str(headers.get("authorization") or "").removeprefix("Bearer "),
        }


def build_mattermost_gateway_bridge(app: Any, config: MattermostChannelConfig) -> MattermostInteractionBridge:
    return MattermostInteractionBridge.from_config(
        None,
        config,
        runtime_factory=runtime_factory_for_app(app),
        tool_display=getattr(app, "tool_display", "summary"),
        busy_mode=getattr(app, "channel_busy_mode", "interrupt"),
    )


def validate_mattermost_schedule_target(config: MattermostChannelConfig, delivery: Any) -> None:
    target = delivery.delivery_target
    if not target:
        raise RuntimeError("mattermost schedule delivery requires target")
    if config.allowed_channels and target not in set(config.allowed_channels):
        raise RuntimeError("mattermost schedule delivery target is not allowed by allowed_channels")
