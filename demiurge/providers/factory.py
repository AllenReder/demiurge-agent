from __future__ import annotations

from pathlib import Path

from demiurge.providers.anthropic_messages import AnthropicMessagesProvider
from demiurge.providers.base import Provider, ProviderFactoryConfig
from demiurge.providers.fake import FakeProvider
from demiurge.providers.openai_chat import OpenAIChatProvider


def create_provider_from_config(
    *,
    config: ProviderFactoryConfig,
    fake_script: Path | None = None,
) -> tuple[Provider, str]:
    if config.provider_id == "fake" or config.api_mode == "fake":
        return FakeProvider(fake_script), "fake"
    if config.api_mode == "openai-chat":
        return OpenAIChatProvider(api_key=config.api_key, base_url=config.base_url), config.provider_id
    if config.api_mode == "anthropic-messages":
        return AnthropicMessagesProvider(api_key=config.api_key, base_url=config.base_url), config.provider_id
    raise ValueError(f"unknown provider api_mode: {config.api_mode}")
