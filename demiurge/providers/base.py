from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from demiurge.providers.profiles import ProviderRuntimeProfile
from demiurge.providers.types import LLMRequest, LLMResponse


class Provider(Protocol):
    async def complete(self, request: LLMRequest) -> LLMResponse:
        ...


@dataclass(frozen=True, slots=True)
class ProviderFactoryConfig:
    provider_id: str
    api_mode: str
    base_url: str | None = None
    api_key: str | None = None
    runtime_profile: ProviderRuntimeProfile | None = None


class ProviderTransport(Protocol):
    api_mode: str

    def build_payload(self, request: LLMRequest) -> dict:
        ...

    def normalize_response(self, response: object) -> LLMResponse:
        ...


def resolve_fake_script(script_path: Path | None) -> Path | None:
    return script_path
