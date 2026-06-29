from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from demiurge.channels.telegram import TelegramInteractionBridge, build_telegram_gateway_bridge
from demiurge.scheduler import start_scheduler_for_app


@dataclass(slots=True)
class GatewayChannel:
    name: str
    bridge: TelegramInteractionBridge


class GatewayConfigError(RuntimeError):
    pass


def build_enabled_gateway_channels(app: Any) -> list[GatewayChannel]:
    core = app.core_loader.load(app.version_store.active_core_path(app.runner.core_id))
    channels: list[GatewayChannel] = []
    for name, config in core.manifest.channels.items():
        if not getattr(config, "enabled", False):
            continue
        if name != "telegram":
            raise GatewayConfigError(f"unsupported enabled gateway channel for core `{core.core_id}`: {name}")
        try:
            bridge = build_telegram_gateway_bridge(app, config)
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
    telegram_bridge = next((channel.bridge for channel in channels if channel.name == "telegram"), None)
    scheduler = start_scheduler_for_app(app, delivery_bridge=telegram_bridge)
    try:
        await asyncio.gather(*(channel.bridge.run_forever() for channel in channels))
    finally:
        if scheduler is not None:
            await scheduler.stop()
