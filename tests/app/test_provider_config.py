from demiurge.app import (
    create_app,
    create_provider,
    resolve_api_key,
    resolve_base_url,
    resolve_model_name,
    resolve_model_options,
    resolve_tool_display,
)
from demiurge.core import ModelInfo, UiInfo
from demiurge.providers import FakeProvider, OpenAICompatibleProvider


def test_create_provider_uses_core_base_url(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    provider, name = create_provider(
        provider_name="auto",
        model_info=ModelInfo(
            provider="openai-compatible",
            model_name="custom-model",
            base_url="https://llm.example.test/v1",
        ),
    )

    assert name == "openai"
    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.base_url == "https://llm.example.test/v1"


def test_cli_provider_choice_overrides_core_provider(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    provider, name = create_provider(
        provider_name="fake",
        model_info=ModelInfo(
            provider="openai-compatible",
            model_name="custom-model",
            base_url="https://llm.example.test/v1",
        ),
    )

    assert name == "fake"
    assert isinstance(provider, FakeProvider)


def test_create_provider_uses_fallback_provider_when_core_provider_is_empty():
    provider, name = create_provider(
        provider_name="auto",
        model_info=ModelInfo(),
        fallback_model_info=ModelInfo(provider="fake"),
    )

    assert name == "fake"
    assert isinstance(provider, FakeProvider)


def test_base_url_argument_overrides_core_base_url(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    provider, _ = create_provider(
        provider_name="openai",
        model_info=ModelInfo(
            provider="openai-compatible",
            model_name="custom-model",
            base_url="https://core.example.test/v1",
        ),
        base_url="https://cli.example.test/v1",
    )

    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.base_url == "https://cli.example.test/v1"


def test_model_base_url_and_api_key_env_resolution(monkeypatch):
    monkeypatch.setenv("DEMIURGE_TEST_MODEL", "env-model")
    monkeypatch.setenv("DEMIURGE_TEST_BASE_URL", "https://env.example.test/v1")
    monkeypatch.setenv("DEMIURGE_TEST_API_KEY", "env-key")
    info = ModelInfo(
        provider="openai-compatible",
        model_name="direct-model",
        model_name_env="DEMIURGE_TEST_MODEL",
        base_url="https://direct.example.test/v1",
        base_url_env="DEMIURGE_TEST_BASE_URL",
        api_key="direct-key",
        api_key_env="DEMIURGE_TEST_API_KEY",
    )

    assert resolve_model_name(info) == ("env-model", "env:DEMIURGE_TEST_MODEL")
    assert resolve_base_url(info) == ("https://env.example.test/v1", "env:DEMIURGE_TEST_BASE_URL")
    assert resolve_api_key(info) == ("env-key", "env:DEMIURGE_TEST_API_KEY")


def test_direct_values_are_used_when_env_names_are_unset(monkeypatch):
    monkeypatch.delenv("DEMIURGE_TEST_MODEL", raising=False)
    monkeypatch.delenv("DEMIURGE_TEST_BASE_URL", raising=False)
    monkeypatch.delenv("DEMIURGE_TEST_API_KEY", raising=False)
    info = ModelInfo(
        provider="openai-compatible",
        model_name="direct-model",
        model_name_env="DEMIURGE_TEST_MODEL",
        base_url="https://direct.example.test/v1",
        base_url_env="DEMIURGE_TEST_BASE_URL",
        api_key="direct-key",
        api_key_env="DEMIURGE_TEST_API_KEY",
    )

    assert resolve_model_name(info) == ("direct-model", "agent.yaml:model.model_name")
    assert resolve_base_url(info) == ("https://direct.example.test/v1", "agent.yaml:model.base_url")
    assert resolve_api_key(info) == ("direct-key", "agent.yaml:model.api_key")


def test_cli_overrides_model_base_url_and_api_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")
    info = ModelInfo(
        provider="openai-compatible",
        model_name="direct-model",
        base_url="https://direct.example.test/v1",
        api_key="direct-key",
    )

    assert resolve_model_name(info, override="cli-model") == ("cli-model", "cli")
    assert resolve_base_url(info, override="https://cli.example.test/v1") == ("https://cli.example.test/v1", "cli")
    assert resolve_api_key(info, override="cli-key") == ("cli-key", "cli")


def test_fallback_model_config_is_used_when_core_value_is_empty(monkeypatch):
    monkeypatch.delenv("DEMIURGE_TEST_MODEL", raising=False)
    monkeypatch.delenv("DEMIURGE_FALLBACK_MODEL", raising=False)
    core = ModelInfo(model_name_env="DEMIURGE_TEST_MODEL", model_options={"temperature": 0.1})
    fallback = ModelInfo(
        model_name="fallback-model",
        model_name_env="DEMIURGE_FALLBACK_MODEL",
        base_url="https://fallback.example.test/v1",
        api_key="fallback-key",
        model_options={"temperature": 0.7, "max_tokens": 512},
    )

    assert resolve_model_name(core, fallback) == ("fallback-model", "agents/agent.yaml:model.model_name")
    assert resolve_base_url(core, fallback) == ("https://fallback.example.test/v1", "agents/agent.yaml:model.base_url")
    assert resolve_api_key(core, fallback) == ("fallback-key", "agents/agent.yaml:model.api_key")
    assert resolve_model_options(core, fallback) == {"temperature": 0.1, "max_tokens": 512}


def test_fallback_env_value_wins_over_fallback_direct_value(monkeypatch):
    monkeypatch.setenv("DEMIURGE_FALLBACK_MODEL", "fallback-env-model")
    core = ModelInfo()
    fallback = ModelInfo(model_name="fallback-direct-model", model_name_env="DEMIURGE_FALLBACK_MODEL")

    assert resolve_model_name(core, fallback) == ("fallback-env-model", "env:DEMIURGE_FALLBACK_MODEL")


def test_core_direct_value_wins_over_fallback(monkeypatch):
    monkeypatch.setenv("DEMIURGE_FALLBACK_MODEL", "fallback-env-model")
    core = ModelInfo(model_name="core-model")
    fallback = ModelInfo(model_name_env="DEMIURGE_FALLBACK_MODEL", model_name="fallback-direct-model")

    assert resolve_model_name(core, fallback) == ("core-model", "agent.yaml:model.model_name")


def test_standard_model_env_is_used_before_internal_fallback(monkeypatch):
    monkeypatch.setenv("DEMIURGE_MODEL_NAME", "standard-env-model")

    assert resolve_model_name(ModelInfo(), ModelInfo()) == ("standard-env-model", "env:DEMIURGE_MODEL_NAME")


def test_model_name_falls_back_to_internal_fake_model(monkeypatch):
    monkeypatch.delenv("DEMIURGE_MODEL_NAME", raising=False)

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
    try:
        resolve_tool_display(UiInfo(tool_display="loud"), UiInfo())
    except ValueError as exc:
        assert "invalid tool_display" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_create_app_exposes_source_agent_model_env_config(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMIURGE_MODEL_NAME", "env-assistant-model")
    monkeypatch.setenv("DEMIURGE_BASE_URL", "https://env.example.test/v1")
    app = create_app(home=tmp_path / "home", provider_name="fake")

    status = app.status()

    assert status["model"] == "env-assistant-model"
    assert status["model_source"] == "env:DEMIURGE_MODEL_NAME"
    assert status["base_url"] == "https://env.example.test/v1"
    assert status["base_url_source"] == "env:DEMIURGE_BASE_URL"
    assert status["fallback_config"].endswith("agents/agent.yaml")
    assert status["tool_display"] == "summary"
    assert status["tool_display_source"] == "agents/agent.yaml:ui.tool_display"
