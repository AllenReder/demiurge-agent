from __future__ import annotations

import hashlib
import json

from demiurge.security.redaction import (
    REDACTION_FAILED,
    RedactionView,
    SecretRedactor,
    SecretValue,
    redact_exception,
)


def test_secret_value_has_distinct_safe_views():
    secret = SecretValue(
        value="SYNTHETIC_ENV_SECRET",
        name="OPENAI_API_KEY",
        source="env:OPENAI_API_KEY",
    )
    redactor = SecretRedactor((secret,))

    assert redactor.redact(secret, view=RedactionView.MODEL).value == "<redacted>"
    assert redactor.redact(secret, view=RedactionView.OPERATOR).value == (
        "<redacted:OPENAI_API_KEY>"
    )
    assert redactor.redact(secret, view=RedactionView.EVENT).value == {
        "redacted": True,
        "name": "OPENAI_API_KEY",
        "source": "env:OPENAI_API_KEY",
    }
    assert redactor.redact(secret, view=RedactionView.DURABLE).value == {
        "redacted": True,
        "name": "OPENAI_API_KEY",
        "source": "env:OPENAI_API_KEY",
    }
    assert redactor.redact(secret, view=RedactionView.DEBUG).value == {
        "redacted": True,
        "name": "OPENAI_API_KEY",
        "source": "env:OPENAI_API_KEY",
        "fingerprint": hashlib.sha256(b"SYNTHETIC_ENV_SECRET").hexdigest()[:16],
    }


def test_structured_redaction_corpus_never_exposes_secret_values():
    payload = {
        "api_key": "SYNTHETIC_API_KEY",
        "env": {
            "DEMIURGE_PROVIDER_SECRET": "SYNTHETIC_ENV_VALUE",
        },
        "url": (
            "https://demo:SYNTHETIC_URL_PASSWORD@example.test/data"
            "?access_token=SYNTHETIC_QUERY_TOKEN&mode=summary"
        ),
        "headers": {
            "Authorization": "Bearer SYNTHETIC_AUTH_TOKEN",
            "X-Request-ID": "request-123",
        },
        "nested": {
            "password": "SYNTHETIC_JSON_PASSWORD",
            "token_count": 42,
        },
        "command": (
            "curl --token SYNTHETIC_INLINE_TOKEN "
            "-H 'Authorization: Bearer SYNTHETIC_HEADER_TOKEN' "
            "https://example.test?api_key=SYNTHETIC_COMMAND_QUERY"
        ),
        "exception": (
            "provider failed: Authorization: Bearer SYNTHETIC_EXCEPTION_TOKEN "
            "password=SYNTHETIC_EXCEPTION_PASSWORD"
        ),
    }
    secrets = {
        "SYNTHETIC_API_KEY",
        "SYNTHETIC_ENV_VALUE",
        "SYNTHETIC_URL_PASSWORD",
        "SYNTHETIC_QUERY_TOKEN",
        "SYNTHETIC_AUTH_TOKEN",
        "SYNTHETIC_JSON_PASSWORD",
        "SYNTHETIC_INLINE_TOKEN",
        "SYNTHETIC_HEADER_TOKEN",
        "SYNTHETIC_COMMAND_QUERY",
        "SYNTHETIC_EXCEPTION_TOKEN",
        "SYNTHETIC_EXCEPTION_PASSWORD",
    }
    redactor = SecretRedactor.from_value(payload)

    for view in RedactionView:
        result = redactor.redact(payload, view=view)
        encoded = json.dumps(result.value, ensure_ascii=False, sort_keys=True)

        assert result.failed is False
        assert not any(secret in encoded for secret in secrets)
        assert "request-123" in encoded
        assert '"token_count": 42' in encoded
        assert {item["source"] for item in result.secret_sources} >= {
            "field:api_key",
            "field:env.DEMIURGE_PROVIDER_SECRET",
            "field:headers.Authorization",
            "field:nested.password",
            "url:url.userinfo",
            "url:url.query.access_token",
        }


def test_percent_encoded_url_secret_is_redacted_without_hiding_public_query():
    encoded_secret = "SYNTHETIC%5FENCODED%5FQUERY%5FSECRET"
    payload = {
        "url": (
            "https://example.test/callback?"
            f"access_token={encoded_secret}&mode=summary"
        )
    }
    redactor = SecretRedactor.from_value(payload)

    for view in RedactionView:
        rendered = redactor.redact(payload, view=view)
        encoded = json.dumps(rendered.value, ensure_ascii=False)

        assert rendered.failed is False
        assert encoded_secret not in encoded
        assert "SYNTHETIC_ENCODED_QUERY_SECRET" not in encoded
        assert "mode=summary" in encoded


def test_known_secret_mapping_keys_and_sensitive_containers_are_opaque():
    key_secret = "SYNTHETIC_SECRET_MAPPING_KEY"
    nested_secret = "SYNTHETIC_NESTED_PASSWORD_VALUE"
    payload = {
        "api_key": key_secret,
        "metadata": {key_secret: "public"},
        "password": [nested_secret],
        "echo": nested_secret,
    }
    redactor = SecretRedactor.from_value(payload)

    for view in RedactionView:
        result = redactor.redact(payload, view=view)
        encoded = json.dumps(result.value, ensure_ascii=False, sort_keys=True)

        assert result.failed is False
        assert key_secret not in encoded
        assert nested_secret not in encoded
        assert "public" in encoded


def test_exception_string_failure_is_fail_closed():
    class BrokenException(RuntimeError):
        def __str__(self):
            raise RuntimeError("string conversion failed")

    rendered = redact_exception(BrokenException())

    assert rendered == "BrokenException: <redaction-failed>"


def test_redaction_failure_returns_fixed_fail_closed_value(monkeypatch):
    payload = {"token": "SYNTHETIC_FAIL_CLOSED_SECRET"}
    redactor = SecretRedactor.from_value(payload)

    def fail(*_args, **_kwargs):
        raise RuntimeError("redactor exploded with SYNTHETIC_FAIL_CLOSED_SECRET")

    monkeypatch.setattr(SecretRedactor, "_redact_value", fail)

    result = redactor.redact(payload, view=RedactionView.DEBUG)

    assert result.failed is True
    assert result.value == REDACTION_FAILED
    assert "SYNTHETIC_FAIL_CLOSED_SECRET" not in json.dumps(result.as_dict())


def test_provider_and_channel_exception_text_is_structurally_redacted():
    secret = "SYNTHETIC_EXCEPTION_AUTH"
    query_secret = "SYNTHETIC_EXCEPTION_QUERY"
    exc = RuntimeError(
        "provider failed\n"
        f"Authorization: Bearer {secret} "
        f"https://example.test/callback?token={query_secret}"
    )

    rendered = redact_exception(exc, view=RedactionView.OPERATOR)

    assert rendered.startswith("RuntimeError: provider failed ")
    assert secret not in rendered
    assert query_secret not in rendered
    assert "<redacted:AUTHORIZATION>" in rendered
    assert "<redacted:TOKEN>" in rendered
