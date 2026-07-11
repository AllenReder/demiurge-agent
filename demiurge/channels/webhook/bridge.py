from __future__ import annotations

import asyncio
from typing import Any

from demiurge.channels.base import TextChannelBridgeBase, resolve_env_value
from demiurge.channels.http import json_request, require_public_http_url
from demiurge.channels.webhook_server import WebhookHttpServer
from demiurge.core import WebhookChannelConfig
from demiurge.runtime.conversation_keys import build_conversation_key
from demiurge.runtime.interaction_factory import runtime_factory_for_app
from demiurge.runtime.interactions import InteractionInbound, InteractionRuntime


class WebhookInteractionBridge(TextChannelBridgeBase):
    def __init__(
        self,
        *,
        config: WebhookChannelConfig,
        runtime: InteractionRuntime | None = None,
        runtime_factory=None,
        tool_display: str = "summary",
        busy_mode: str = "interrupt",
    ) -> None:
        super().__init__(
            channel_name="webhook",
            runtime=runtime,
            runtime_factory=runtime_factory,
            tool_display=tool_display,
            busy_mode=busy_mode,
        )
        self.config = config
        self.token = resolve_env_value(config.token_env, config.token)
        self.callback_url = resolve_env_value(config.callback_url_env, config.callback_url)
        if self.callback_url and not config.allow_private_callback_urls:
            require_public_http_url(self.callback_url)
        self.allowed_sources = set(config.allowed_sources)
        self.delivery_targets = dict(config.delivery_targets)
        self.server = WebhookHttpServer(host=config.host, port=config.port, path=config.path, handler=self._handle_request)

    @classmethod
    def from_config(cls, runtime: InteractionRuntime | None, config: WebhookChannelConfig, **kwargs: Any) -> "WebhookInteractionBridge":
        if not config.allow_unauthenticated and not resolve_env_value(config.token_env, config.token):
            raise RuntimeError("webhook channel requires token_env with a value, token, or allow_unauthenticated: true")
        return cls(config=config, runtime=runtime, **kwargs)

    async def run_forever(self) -> None:
        await self.server.run_forever()

    async def _handle_request(self, request: dict[str, Any]) -> dict[str, Any]:
        headers = request.get("headers") or {}
        body = request.get("body") or {}
        if not isinstance(body, dict):
            return {"status": 400, "body": {"error": "webhook body must be an object"}}
        if not self._authorized(headers, body):
            return {"status": 401, "body": {"error": "unauthorized"}}
        inbound = self.normalize_request(
            body,
            headers=headers,
            client=request.get("client"),
            principal_key=self._request_principal_key(request.get("client")),
        )
        if inbound is None:
            return {"status": 400, "body": {"error": "webhook text is required"}}
        await self.handle_inbound(inbound)
        return {"status": 202, "body": {"ok": True, "conversation_key": inbound.conversation_key}}

    def normalize_request(
        self,
        body: dict[str, Any],
        *,
        headers: dict[str, str] | None = None,
        client: str | None = None,
        principal_key: str | None = None,
    ) -> InteractionInbound | None:
        text = str(body.get("text") or body.get("prompt") or "").strip()
        if not text:
            return None
        source = str(body.get("source") or body.get("user") or client or "webhook")
        if self.allowed_sources and source not in self.allowed_sources:
            return None
        conversation_key = str(body.get("conversation_key") or build_conversation_key("webhook", "source", source))
        metadata = dict(body.get("metadata") if isinstance(body.get("metadata"), dict) else {})
        callback_url = body.get("callback_url") or body.get("response_url") or self.callback_url
        if callback_url:
            callback_url = str(callback_url)
            if not self.config.allow_private_callback_urls:
                require_public_http_url(callback_url)
            metadata["webhook_callback_url"] = callback_url
        metadata.update(
            {
                "webhook_source": source,
                "webhook_client": client,
                "webhook_headers": dict(headers or {}),
            }
        )
        reply_to = body.get("reply_to") or body.get("request_id")
        return InteractionInbound(
            channel="webhook",
            text=text,
            source=source,
            principal_key=principal_key or self._request_principal_key(client),
            reply_to=str(reply_to) if reply_to is not None else None,
            conversation_key=conversation_key,
            metadata=metadata,
        )

    def _request_principal_key(self, client: str | None) -> str:
        if not self.config.allow_unauthenticated:
            return "configured-token"
        return f"client:{client or 'unknown'}"

    async def _send_text(
        self,
        source: str,
        text: str,
        *,
        reply_to: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Any:
        callback_url = (metadata or {}).get("webhook_callback_url") or self.callback_url or self.delivery_targets.get(source)
        if not callback_url:
            return None
        payload = {
            "channel": "webhook",
            "source": source,
            "reply_to": reply_to,
            "text": text,
        }
        return await asyncio.to_thread(
            json_request,
            str(callback_url),
            payload=payload,
            allow_private=self.config.allow_private_callback_urls,
        )

    def _authorized(self, headers: dict[str, str], body: dict[str, Any]) -> bool:
        if self.config.allow_unauthenticated:
            return True
        token = self.token
        if not token:
            return False
        authorization = str(headers.get("authorization") or "")
        if authorization == f"Bearer {token}":
            return True
        if str(headers.get("x-demiurge-token") or "") == token:
            return True
        return str(body.get("token") or "") == token


def build_webhook_gateway_bridge(app: Any, config: WebhookChannelConfig) -> WebhookInteractionBridge:
    return WebhookInteractionBridge.from_config(
        None,
        config,
        runtime_factory=runtime_factory_for_app(app),
        tool_display=getattr(app, "tool_display", "summary"),
        busy_mode=getattr(app, "channel_busy_mode", "interrupt"),
    )


def validate_webhook_schedule_target(config: WebhookChannelConfig, delivery: Any) -> None:
    target = delivery.delivery_target
    if not target:
        raise RuntimeError("webhook schedule delivery requires target")
    if target not in config.delivery_targets:
        raise RuntimeError("webhook schedule delivery target is not configured in delivery_targets")
