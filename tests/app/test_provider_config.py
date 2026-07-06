import pytest

from demiurge.app import (
    HostConfig,
    HostProviderProfile,
    create_app,
    create_provider,
    resolve_model_name,
    resolve_model_options,
    resolve_profile_api_key,
    resolve_provider_config,
    resolve_tool_display,
)
from demiurge.core import ModelInfo, UiInfo
from demiurge.providers import AnthropicMessagesProvider, FakeProvider, OpenAIChatProvider


def _host_config_with_profile(
    provider_id: str = "deepseek",
    *,
    base_url: str = "https://llm.example.test/v1",
    api_key_env: str | None = "DEMIURGE_TEST_API_KEY",
    api_key: str | None = None,
    default: str | None = None,
    api_mode: str = "openai-chat",
) -> HostConfig:
    return HostConfig(
        providers={
            "default": default,
            "profiles": {
                provider_id: {
                    "api_mode": api_mode,
                    "base_url": base_url,
                    "api_key_env": api_key_env,
                    "api_key": api_key,
                }
            },
        }
    )


def test_create_provider_uses_host_profile_base_url_and_key(monkeypatch):
    monkeypatch.setenv("DEMIURGE_TEST_API_KEY", "test-key")
    config = _host_config_with_profile()
    resolved = resolve_provider_config(config, ModelInfo(provider="deepseek", model_name="custom-model"))

    provider, name = create_provider(provider_config=resolved)

    assert name == "deepseek"
    assert isinstance(provider, OpenAIChatProvider)
    assert provider.base_url == "https://llm.example.test/v1"
    assert provider.api_key == "test-key"
    assert resolved.api_mode == "openai-chat"
    assert resolved.api_mode_source == "config.yaml:providers.profiles.deepseek.api_mode"
    assert resolved.api_key_source == "env:DEMIURGE_TEST_API_KEY"


def test_cli_provider_choice_overrides_core_provider(monkeypatch):
    monkeypatch.setenv("DEMIURGE_TEST_API_KEY", "test-key")
    config = _host_config_with_profile()
    resolved = resolve_provider_config(config, ModelInfo(provider="deepseek"), override="fake")

    provider, name = create_provider(provider_config=resolved)

    assert name == "fake"
    assert isinstance(provider, FakeProvider)


def test_host_default_provider_is_used_when_core_provider_is_auto(monkeypatch):
    monkeypatch.setenv("DEMIURGE_TEST_API_KEY", "test-key")
    config = _host_config_with_profile(default="deepseek")
    resolved = resolve_provider_config(config, ModelInfo(provider="auto"))

    assert resolved.provider_id == "deepseek"
    assert resolved.provider_source == "config.yaml:providers.default"


def test_auto_provider_without_default_falls_back_to_fake():
    resolved = resolve_provider_config(HostConfig(), ModelInfo())
    provider, name = create_provider(provider_config=resolved)

    assert resolved.provider_id == "fake"
    assert name == "fake"
    assert isinstance(provider, FakeProvider)


def test_builtin_provider_profile_can_be_selected_with_standard_env(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    resolved = resolve_provider_config(HostConfig(), ModelInfo(provider="openai"))

    provider, name = create_provider(provider_config=resolved)

    assert name == "openai"
    assert isinstance(provider, OpenAIChatProvider)
    assert provider.base_url == "https://api.openai.com/v1"
    assert provider.api_key == "openai-key"
    assert resolved.api_mode == "openai-chat"
    assert resolved.base_url_source == "builtin:openai.base_url"


def test_anthropic_provider_profile_selects_messages_api_mode(monkeypatch):
    monkeypatch.setenv("DEMIURGE_TEST_API_KEY", "test-key")
    config = _host_config_with_profile(
        provider_id="claude",
        base_url="https://api.anthropic.com/v1",
        api_mode="anthropic-messages",
    )
    resolved = resolve_provider_config(config, ModelInfo(provider="claude", model_name="claude-test"))

    provider, name = create_provider(provider_config=resolved)

    assert name == "claude"
    assert isinstance(provider, AnthropicMessagesProvider)
    assert provider.endpoint == "https://api.anthropic.com/v1/messages"
    assert resolved.api_mode == "anthropic-messages"


def test_builtin_anthropic_preset_uses_messages_api_mode(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-key")
    resolved = resolve_provider_config(HostConfig(), ModelInfo(provider="anthropic"))

    provider, name = create_provider(provider_config=resolved)

    assert name == "anthropic"
    assert isinstance(provider, AnthropicMessagesProvider)
    assert provider.api_key == "anthropic-key"
    assert resolved.api_mode == "anthropic-messages"


def test_env_api_key_wins_over_direct_api_key(monkeypatch):
    profile = HostProviderProfile(
        base_url="https://direct.example.test/v1",
        api_key_env="DEMIURGE_TEST_API_KEY",
        api_key="direct-key",
    )
    monkeypatch.setenv("DEMIURGE_TEST_API_KEY", "env-key")

    assert resolve_profile_api_key(profile, provider_id="test") == ("env-key", "env:DEMIURGE_TEST_API_KEY")

    monkeypatch.delenv("DEMIURGE_TEST_API_KEY", raising=False)
    assert resolve_profile_api_key(profile, provider_id="test") == ("direct-key", "config.yaml:providers.profile.api_key")


def test_cli_api_key_override_wins_over_profile_values(monkeypatch):
    monkeypatch.setenv("DEMIURGE_TEST_API_KEY", "env-key")
    config = _host_config_with_profile(api_key="direct-key")
    resolved = resolve_provider_config(
        config,
        ModelInfo(provider="deepseek"),
        api_key_override="cli-key",
    )

    assert resolved.api_key == "cli-key"
    assert resolved.api_key_source == "cli"


def test_fallback_model_config_is_used_when_core_value_is_empty():
    core = ModelInfo(model_options={"temperature": 0.1})
    fallback = ModelInfo(model_name="fallback-model", model_options={"temperature": 0.7, "max_tokens": 512})

    assert resolve_model_name(core, fallback) == ("fallback-model", "agents/agent.yaml:model.model_name")
    assert resolve_model_options(core, fallback) == {"temperature": 0.1, "max_tokens": 512}


def test_core_direct_model_value_wins_over_fallback():
    core = ModelInfo(model_name="core-model")
    fallback = ModelInfo(model_name="fallback-direct-model")

    assert resolve_model_name(core, fallback) == ("core-model", "agent.yaml:model.model_name")


def test_model_name_falls_back_to_internal_fake_model():
    assert resolve_model_name(ModelInfo(), ModelInfo()) == ("fake/demo", "default")


def test_tool_display_resolution_uses_core_then_fallback_then_default():
    assert resolve_tool_display(UiInfo(), UiInfo()) == ("summary", "default")
    assert resolve_tool_display(UiInfo(), UiInfo(tool_display="full")) == (
        "full",
        "agents/agent.yaml:ui.tool_display",
    )
    assert resolve_tool_display(UiInfo(tool_display="quiet"), UiInfo(tool_display="full")) == (
        "quiet",
        "agent.yaml:ui.tool_display",
    )
    assert resolve_tool_display(UiInfo(tool_display="quiet"), UiInfo(tool_display="full"), override="summary") == (
        "summary",
        "cli",
    )


def test_tool_display_rejects_invalid_config_value():
    with pytest.raises(ValueError, match="invalid tool_display"):
        resolve_tool_display(UiInfo(tool_display="loud"), UiInfo())


def test_create_app_loads_runtime_env_file_over_shell_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMIURGE_TEST_API_KEY", "shell-key")
    home = tmp_path / "home"
    home.mkdir()
    (home / ".env").write_text('DEMIURGE_TEST_API_KEY="file-key"\n', encoding="utf-8")
    (home / "config.yaml").write_text(
        "providers:\n"
        "  default: deepseek\n"
        "  profiles:\n"
        "    deepseek:\n"
        "      base_url: https://env.example.test/v1\n"
        "      api_key_env: DEMIURGE_TEST_API_KEY\n",
        encoding="utf-8",
    )

    app = create_app(home=home)

    assert app.provider_name == "deepseek"
    assert app.base_url == "https://env.example.test/v1"
    assert app.api_key_source == "env:DEMIURGE_TEST_API_KEY"
    assert isinstance(app.runner.provider, OpenAIChatProvider)
    assert app.runner.provider.api_key == "file-key"
