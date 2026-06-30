from __future__ import annotations

import asyncio
import time
from typing import Any

from demiurge.channels.base import TextChannelBridgeBase, resolve_env_value, runtime_factory_for_app
from demiurge.channels.matrix.api import MatrixApi
from demiurge.core import MatrixChannelConfig
from demiurge.runtime.interactions import InteractionInbound, InteractionRuntime


class MatrixInteractionBridge(TextChannelBridgeBase):
    def __init__(
        self,
        *,
        api: MatrixApi,
        config: MatrixChannelConfig,
        runtime: InteractionRuntime | None = None,
        runtime_factory=None,
        tool_display: str = "summary",
        busy_mode: str = "interrupt",
    ) -> None:
        super().__init__(
            channel_name="matrix",
            runtime=runtime,
            runtime_factory=runtime_factory,
            tool_display=tool_display,
            busy_mode=busy_mode,
        )
        self.api = api
        self.config = config
        self.allowed_rooms = set(config.allowed_rooms)
        self.since: str | None = None
        self._initialized = False
        self._txn_counter = 0

    @classmethod
    def from_config(cls, runtime: InteractionRuntime | None, config: MatrixChannelConfig, **kwargs: Any) -> "MatrixInteractionBridge":
        access_token = resolve_env_value(config.access_token_env, config.access_token)
        if not config.homeserver_url:
            raise RuntimeError("matrix channel requires homeserver_url")
        if not access_token:
            raise RuntimeError("matrix channel requires access_token_env with a value or access_token")
        return cls(api=MatrixApi(homeserver_url=config.homeserver_url, access_token=access_token), config=config, runtime=runtime, **kwargs)

    async def run_forever(self) -> None:
        while True:
            data = await asyncio.to_thread(self.api.sync, since=self.since, timeout_ms=self.config.poll_timeout * 1000)
            self.since = str(data.get("next_batch") or self.since or "") or None
            if not self._initialized:
                self._initialized = True
                await asyncio.sleep(0.1)
                continue
            for inbound in self.normalize_sync(data):
                await self.handle_inbound(inbound)
            await asyncio.sleep(0.1)

    def normalize_sync(self, data: dict[str, Any]) -> list[InteractionInbound]:
        joined = ((data.get("rooms") or {}).get("join") or {})
        inbound: list[InteractionInbound] = []
        for room_id, room in joined.items():
            room_id = str(room_id)
            if self.allowed_rooms and room_id not in self.allowed_rooms:
                continue
            events = ((room or {}).get("timeline") or {}).get("events") or []
            for event in events:
                item = self.normalize_event(room_id, event)
                if item is not None:
                    inbound.append(item)
        return inbound

    def normalize_event(self, room_id: str, event: dict[str, Any]) -> InteractionInbound | None:
        if event.get("type") != "m.room.message":
            return None
        if self.config.user_id and event.get("sender") == self.config.user_id:
            return None
        content = event.get("content") if isinstance(event.get("content"), dict) else {}
        if content.get("msgtype") not in {"m.text", "m.notice"}:
            return None
        text = str(content.get("body") or "").strip()
        if not text:
            return None
        event_id = str(event.get("event_id") or "") or None
        sender = str(event.get("sender") or "")
        return InteractionInbound(
            channel="matrix",
            text=text,
            source=room_id,
            reply_to=event_id,
            conversation_key=f"matrix:{room_id}",
            metadata={
                "matrix_room_id": room_id,
                "matrix_event_id": event_id,
                "matrix_sender": sender,
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
        room_id = str((metadata or {}).get("matrix_room_id") or source)
        return await asyncio.to_thread(self.api.send_message, room_id=room_id, body=text, txn_id=self._next_txn_id())

    def _next_txn_id(self) -> str:
        self._txn_counter += 1
        return f"demiurge-{int(time.time() * 1000)}-{self._txn_counter}"


def build_matrix_gateway_bridge(app: Any, config: MatrixChannelConfig) -> MatrixInteractionBridge:
    return MatrixInteractionBridge.from_config(
        None,
        config,
        runtime_factory=runtime_factory_for_app(app),
        tool_display=getattr(app, "tool_display", "summary"),
        busy_mode=getattr(app, "channel_busy_mode", "interrupt"),
    )


def validate_matrix_schedule_target(config: MatrixChannelConfig, delivery: Any) -> None:
    target = delivery.delivery_target
    if not target:
        raise RuntimeError("matrix schedule delivery requires target")
    if config.allowed_rooms and target not in set(config.allowed_rooms):
        raise RuntimeError("matrix schedule delivery target is not allowed by allowed_rooms")
