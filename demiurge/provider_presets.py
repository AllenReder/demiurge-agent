from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProviderPreset:
    preset_id: str
    label: str
    base_url: str
    api_key_env: str
    suggested_model: str | None
    api_mode: str = "openai-chat"


BUILTIN_PROVIDER_PRESETS: tuple[ProviderPreset, ...] = (
    ProviderPreset(
        preset_id="openai",
        label="OpenAI",
        base_url="https://api.openai.com/v1",
        api_key_env="OPENAI_API_KEY",
        suggested_model="gpt-5.5",
    ),
    ProviderPreset(
        preset_id="anthropic",
        label="Anthropic",
        base_url="https://api.anthropic.com/v1",
        api_key_env="ANTHROPIC_API_KEY",
        suggested_model=None,
        api_mode="anthropic-messages",
    ),
    ProviderPreset(
        preset_id="deepseek",
        label="DeepSeek",
        base_url="https://api.deepseek.com",
        api_key_env="DEEPSEEK_API_KEY",
        suggested_model="deepseek-v4-pro",
    ),
    ProviderPreset(
        preset_id="moonshot",
        label="Kimi/Moonshot",
        base_url="https://api.moonshot.ai/v1",
        api_key_env="MOONSHOT_API_KEY",
        suggested_model="kimi-k2.7-code",
    ),
    ProviderPreset(
        preset_id="minimax",
        label="MiniMax",
        base_url="https://api.minimax.io/v1",
        api_key_env="MINIMAX_API_KEY",
        suggested_model="MiniMax-M3",
    ),
    ProviderPreset(
        preset_id="minimax-cn",
        label="MiniMax-CN",
        base_url="https://api.minimaxi.com/v1",
        api_key_env="MINIMAX_CN_API_KEY",
        suggested_model="MiniMax-M3",
    ),
    ProviderPreset(
        preset_id="dashscope",
        label="Alibaba DashScope/百炼",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key_env="DASHSCOPE_API_KEY",
        suggested_model="qwen3.7-max",
    ),
    ProviderPreset(
        preset_id="zai",
        label="Zhipu/Z.ai",
        base_url="https://api.z.ai/api/paas/v4",
        api_key_env="ZAI_API_KEY",
        suggested_model="glm-5.2",
    ),
    ProviderPreset(
        preset_id="siliconflow",
        label="SiliconFlow",
        base_url="https://api.siliconflow.cn/v1",
        api_key_env="SILICONFLOW_API_KEY",
        suggested_model=None,
    ),
    ProviderPreset(
        preset_id="openrouter",
        label="OpenRouter",
        base_url="https://openrouter.ai/api/v1",
        api_key_env="OPENROUTER_API_KEY",
        suggested_model="openai/gpt-5.5",
    ),
)

BUILTIN_PROVIDER_PRESETS_BY_ID = {preset.preset_id: preset for preset in BUILTIN_PROVIDER_PRESETS}


def get_provider_preset(preset_id: str) -> ProviderPreset | None:
    return BUILTIN_PROVIDER_PRESETS_BY_ID.get(preset_id)
