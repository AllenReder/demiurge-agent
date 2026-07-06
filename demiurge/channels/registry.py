from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from demiurge.channels.base import GatewayBridge
from demiurge.core import (
    EmailChannelConfig,
    MatrixChannelConfig,
    MattermostChannelConfig,
    SlackChannelConfig,
    TelegramChannelConfig,
    WebhookChannelConfig,
)


@dataclass(frozen=True, slots=True)
class ChannelRegistration:
    name: str
    config_type: type[Any]
    build_bridge: Callable[[Any, Any], GatewayBridge]
    validate_schedule_target: Callable[[Any, Any], None] | None = None


_REGISTRY: dict[str, ChannelRegistration] = {}
_BUILTINS_REGISTERED = False


def register_channel(registration: ChannelRegistration) -> None:
    _REGISTRY[registration.name] = registration


def registered_channels() -> dict[str, ChannelRegistration]:
    _ensure_builtin_channels()
    return dict(_REGISTRY)


def get_channel_registration(name: str) -> ChannelRegistration | None:
    _ensure_builtin_channels()
    return _REGISTRY.get(name)


def registration_name_for_config(name: str, config: Any) -> str:
    config_type = getattr(config, "type", None)
    return str(config_type or name).strip().lower().replace("-", "_")


def build_channel_bridge(app: Any, name: str, config: Any) -> GatewayBridge:
    registration = get_channel_registration(registration_name_for_config(name, config))
    if registration is None:
        raise RuntimeError(f"unsupported enabled gateway channel: {name}")
    return registration.build_bridge(app, config)


def validate_schedule_target(name: str, config: Any, delivery: Any) -> None:
    registration = get_channel_registration(registration_name_for_config(name, config))
    if registration is None:
        raise RuntimeError(f"unsupported schedule delivery channel: {name}")
    if registration.validate_schedule_target is not None:
        registration.validate_schedule_target(config, delivery)


def _ensure_builtin_channels() -> None:
    global _BUILTINS_REGISTERED
    if _BUILTINS_REGISTERED:
        return
    from demiurge.channels.telegram.bridge import build_telegram_gateway_bridge
    from demiurge.channels.webhook.bridge import build_webhook_gateway_bridge, validate_webhook_schedule_target
    from demiurge.channels.slack.bridge import build_slack_gateway_bridge, validate_slack_schedule_target
    from demiurge.channels.mattermost.bridge import build_mattermost_gateway_bridge, validate_mattermost_schedule_target
    from demiurge.channels.matrix.bridge import build_matrix_gateway_bridge, validate_matrix_schedule_target
    from demiurge.channels.email.bridge import build_email_gateway_bridge, validate_email_schedule_target

    register_channel(
        ChannelRegistration(
            name="telegram",
            config_type=TelegramChannelConfig,
            build_bridge=build_telegram_gateway_bridge,
            validate_schedule_target=_validate_telegram_schedule_target,
        )
    )
    register_channel(
        ChannelRegistration(
            name="webhook",
            config_type=WebhookChannelConfig,
            build_bridge=build_webhook_gateway_bridge,
            validate_schedule_target=validate_webhook_schedule_target,
        )
    )
    register_channel(
        ChannelRegistration(
            name="slack",
            config_type=SlackChannelConfig,
            build_bridge=build_slack_gateway_bridge,
            validate_schedule_target=validate_slack_schedule_target,
        )
    )
    register_channel(
        ChannelRegistration(
            name="mattermost",
            config_type=MattermostChannelConfig,
            build_bridge=build_mattermost_gateway_bridge,
            validate_schedule_target=validate_mattermost_schedule_target,
        )
    )
    register_channel(
        ChannelRegistration(
            name="matrix",
            config_type=MatrixChannelConfig,
            build_bridge=build_matrix_gateway_bridge,
            validate_schedule_target=validate_matrix_schedule_target,
        )
    )
    register_channel(
        ChannelRegistration(
            name="email",
            config_type=EmailChannelConfig,
            build_bridge=build_email_gateway_bridge,
            validate_schedule_target=validate_email_schedule_target,
        )
    )
    _BUILTINS_REGISTERED = True


def _validate_telegram_schedule_target(config: TelegramChannelConfig, delivery: Any) -> None:
    chat_id = getattr(delivery, "chat_id", None)
    if chat_id is None:
        raise RuntimeError("telegram schedule delivery requires chat_id")
    if chat_id not in set(config.allowed_users) and chat_id not in set(config.allowed_chats):
        raise RuntimeError("telegram schedule delivery target is not allowed by core allowlist")


__all__ = [
    "ChannelRegistration",
    "build_channel_bridge",
    "get_channel_registration",
    "registered_channels",
    "register_channel",
    "validate_schedule_target",
]
