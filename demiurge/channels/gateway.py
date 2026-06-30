from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from demiurge.channels.base import ChannelRouterBridge, GatewayBridge
from demiurge.channels.registry import build_channel_bridge
from demiurge.scheduler import start_scheduler_for_app


@dataclass(slots=True)
class GatewayChannel:
    name: str
    bridge: GatewayBridge


class GatewayConfigError(RuntimeError):
    pass


def build_enabled_gateway_channels(app: Any) -> list[GatewayChannel]:
    core = app.core_loader.load(app.version_store.active_core_path(app.runner.core_id))
    channels: list[GatewayChannel] = []
    for name, config in core.manifest.channels.items():
        if not getattr(config, "enabled", False):
            continue
        try:
            bridge = build_channel_bridge(app, name, config)
        except RuntimeError as exc:
            raise GatewayConfigError(str(exc)) from exc
        channels.append(GatewayChannel(name=name, bridge=bridge))
    if not channels:
        raise GatewayConfigError(f"core `{core.core_id}` has no enabled gateway channels")
    return channels


def run_gateway(app: Any) -> None:
    try:
        asyncio.run(_run_gateway(app))
    except KeyboardInterrupt:
        pass


async def _run_gateway(app: Any) -> None:
    channels = build_enabled_gateway_channels(app)
    channel_map = {channel.name: channel.bridge for channel in channels}
    scheduler = start_scheduler_for_app(
        app,
        delivery_bridge=ChannelRouterBridge(
            channel_map,
            fallback=lambda channel_name: _build_schedule_bridge(app, channel_name, channel_map),
        ),
    )
    try:
        await asyncio.gather(*(channel.bridge.run_forever() for channel in channels))
    finally:
        if scheduler is not None:
            await scheduler.stop()
        close = getattr(app, "close", None)
        if close is not None:
            await close()


def _build_schedule_bridge(app: Any, channel_name: str, channel_map: dict[str, GatewayBridge]) -> GatewayBridge:
    core = app.core_loader.load(app.version_store.active_core_path(app.runner.core_id))
    config = core.manifest.channels.get(channel_name)
    if config is None:
        raise RuntimeError(f"{channel_name} schedule delivery requires channels.{channel_name}")
    bridge = build_channel_bridge(app, channel_name, config)
    channel_map[channel_name] = bridge
    return bridge
