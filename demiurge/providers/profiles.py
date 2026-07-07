from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from demiurge.providers.types import LLMRequest

ApiMode = Literal["openai-chat", "anthropic-messages"]


@dataclass(frozen=True, slots=True)
class ProviderRequestExtras:
    extra_body: dict[str, Any] = field(default_factory=dict)
    top_level_kwargs: dict[str, Any] = field(default_factory=dict)
    extra_headers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProviderRuntimeProfile:
    provider_id: str
    display_name: str
    api_mode: ApiMode
    base_url: str
    env_vars: tuple[str, ...] = ()
    suggested_model: str | None = None
    default_headers: dict[str, str] = field(default_factory=dict)
    default_max_tokens: int | None = None

    def build_request_extras(self, request: LLMRequest, *, base_url: str | None) -> ProviderRequestExtras:
        return ProviderRequestExtras()


def _deepseek_model_supports_thinking(model: str | None) -> bool:
    normalized = (model or "").strip().lower()
    if not normalized:
        return False
    if normalized.startswith("deepseek-v") and not normalized.startswith("deepseek-v3"):
        return True
    return normalized == "deepseek-reasoner"


class DeepSeekRuntimeProfile(ProviderRuntimeProfile):
    def build_request_extras(self, request: LLMRequest, *, base_url: str | None) -> ProviderRequestExtras:
        if not _deepseek_model_supports_thinking(request.model):
            return ProviderRequestExtras()
        return ProviderRequestExtras(extra_body={"thinking": {"type": "enabled"}})


BUILTIN_PROVIDER_PROFILES: tuple[ProviderRuntimeProfile, ...] = (
    ProviderRuntimeProfile(
        provider_id="openai",
        display_name="OpenAI",
        api_mode="openai-chat",
        base_url="https://api.openai.com/v1",
        env_vars=("OPENAI_API_KEY",),
        suggested_model="gpt-5.5",
    ),
    ProviderRuntimeProfile(
        provider_id="anthropic",
        display_name="Anthropic",
        api_mode="anthropic-messages",
        base_url="https://api.anthropic.com/v1",
        env_vars=("ANTHROPIC_API_KEY", "ANTHROPIC_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"),
    ),
    DeepSeekRuntimeProfile(
        provider_id="deepseek",
        display_name="DeepSeek",
        api_mode="openai-chat",
        base_url="https://api.deepseek.com/v1",
        env_vars=("DEEPSEEK_API_KEY",),
        suggested_model="deepseek-v4-pro",
    ),
    ProviderRuntimeProfile(
        provider_id="moonshot",
        display_name="Kimi/Moonshot",
        api_mode="openai-chat",
        base_url="https://api.moonshot.ai/v1",
        env_vars=("KIMI_API_KEY", "KIMI_CODING_API_KEY"),
        suggested_model="kimi-k2.7-code",
        default_headers={"User-Agent": "demiurge-agent"},
        default_max_tokens=32000,
    ),
    ProviderRuntimeProfile(
        provider_id="minimax",
        display_name="MiniMax",
        api_mode="anthropic-messages",
        base_url="https://api.minimax.io/anthropic",
        env_vars=("MINIMAX_API_KEY",),
        suggested_model="MiniMax-M3",
    ),
    ProviderRuntimeProfile(
        provider_id="minimax-cn",
        display_name="MiniMax-CN",
        api_mode="anthropic-messages",
        base_url="https://api.minimaxi.com/anthropic",
        env_vars=("MINIMAX_CN_API_KEY",),
        suggested_model="MiniMax-M3",
    ),
    ProviderRuntimeProfile(
        provider_id="dashscope",
        display_name="Alibaba DashScope/百炼",
        api_mode="openai-chat",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        env_vars=("DASHSCOPE_API_KEY",),
        suggested_model="qwen3.7-max",
    ),
    ProviderRuntimeProfile(
        provider_id="zai",
        display_name="Zhipu/Z.ai",
        api_mode="openai-chat",
        base_url="https://api.z.ai/api/paas/v4",
        env_vars=("ZAI_API_KEY", "GLM_API_KEY", "Z_AI_API_KEY"),
        suggested_model="glm-5.2",
    ),
    ProviderRuntimeProfile(
        provider_id="siliconflow",
        display_name="SiliconFlow",
        api_mode="openai-chat",
        base_url="https://api.siliconflow.cn/v1",
        env_vars=("SILICONFLOW_API_KEY",),
    ),
    ProviderRuntimeProfile(
        provider_id="openrouter",
        display_name="OpenRouter",
        api_mode="openai-chat",
        base_url="https://openrouter.ai/api/v1",
        env_vars=("OPENROUTER_API_KEY",),
        suggested_model="openai/gpt-5.5",
    ),
)

BUILTIN_PROVIDER_PROFILES_BY_ID = {profile.provider_id: profile for profile in BUILTIN_PROVIDER_PROFILES}


def get_builtin_provider_profile(provider_id: str) -> ProviderRuntimeProfile | None:
    return BUILTIN_PROVIDER_PROFILES_BY_ID.get(provider_id)


def is_builtin_provider(provider_id: str) -> bool:
    return provider_id in BUILTIN_PROVIDER_PROFILES_BY_ID
