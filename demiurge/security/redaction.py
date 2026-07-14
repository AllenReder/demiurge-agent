from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlsplit

from demiurge.sdk import ToolResult


REDACTION_FAILED = "<redaction-failed>"
_MODEL_REDACTED = "<redacted>"
_SENSITIVE_FIELD_NAMES = frozenset(
    {
        "access_token",
        "api_key",
        "apikey",
        "auth",
        "authorization",
        "bearer",
        "client_secret",
        "cookie",
        "credential",
        "id_token",
        "key_material",
        "password",
        "passwd",
        "private_key",
        "proxy_authorization",
        "refresh_token",
        "secret",
        "secret_input",
        "secret_value",
        "signature",
        "token",
        "x_access_token",
        "x_api_key",
        "x_api_token",
        "x_auth_token",
        "x_goog_api_key",
    }
)
_SENSITIVE_QUERY_NAMES = _SENSITIVE_FIELD_NAMES | frozenset(
    {"code", "jwt", "key", "session", "x_amz_signature"}
)
_SENSITIVE_COMMAND_NAME = (
    r"(?:access[-_]?token|api[-_]?key|apikey|authorization|cookie|credential|"
    r"password|passwd|private[-_]?key|secret|token)"
)
_COMMAND_OPTION_RE = re.compile(
    rf"(?i)(?:--{_SENSITIVE_COMMAND_NAME})(?:=|\s+)(?:\"([^\"]*)\"|'([^']*)'|([^\s]+))"
)
_ASSIGNMENT_RE = re.compile(
    rf"(?i)\b({_SENSITIVE_COMMAND_NAME})\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s&]+))"
)
_AUTHORIZATION_RE = re.compile(
    r"(?i)(?:Proxy-)?Authorization:\s*(?:[A-Za-z][\w.+-]*\s+)?([^\s\"']+)"
)
_SECRET_HEADER_RE = re.compile(
    r"(?i)(?:x-api-key|x-goog-api-key|api-key|apikey|x-api-token|x-auth-token|x-access-token)\s*:\s*([^\s\"']+)"
)


class RedactionView(str, Enum):
    MODEL = "model"
    OPERATOR = "operator"
    EVENT = "event"
    DURABLE = "durable"
    DEBUG = "debug"


@dataclass(frozen=True, slots=True)
class SecretValue:
    value: str = field(repr=False)
    name: str
    source: str

    def __post_init__(self) -> None:
        if not self.value:
            raise ValueError("secret value must not be empty")
        if not self.name.strip():
            raise ValueError("secret name must not be empty")
        if not self.source.strip():
            raise ValueError("secret source must not be empty")

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.value.encode("utf-8")).hexdigest()[:16]

    def safe_source(self) -> dict[str, str]:
        return {
            "name": self.name,
            "source": self.source,
            "fingerprint": self.fingerprint,
        }


@dataclass(frozen=True, slots=True)
class RedactionResult:
    value: Any
    view: RedactionView
    secret_sources: tuple[dict[str, str], ...] = ()
    failed: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "view": self.view.value,
            "secret_sources": [dict(item) for item in self.secret_sources],
            "failed": self.failed,
        }


class SecretRedactor:
    """Host-owned structured redaction for effect and runtime views.

    The redactor discovers secret values from structured field names and from
    unambiguous URL/header/command forms. Exact discovered values are then
    removed from every string in the payload, so an exception or tool output
    cannot echo a secret under a different field name.
    """

    def __init__(
        self,
        secrets: Iterable[SecretValue] = (),
        *,
        max_depth: int = 8,
        max_items: int = 100,
        max_string_chars: int = 1_000_000,
    ) -> None:
        if max_depth < 1 or max_items < 1 or max_string_chars < 1:
            raise ValueError("redaction limits must be positive")
        unique: dict[tuple[str, str, str], SecretValue] = {}
        for secret in secrets:
            if not isinstance(secret, SecretValue):
                raise TypeError("SecretRedactor secrets must be SecretValue instances")
            unique[(secret.value, secret.name, secret.source)] = secret
        self._secrets = tuple(
            sorted(
                unique.values(),
                key=lambda item: (-len(item.value), item.source, item.name),
            )
        )
        self.max_depth = max_depth
        self.max_items = max_items
        self.max_string_chars = max_string_chars

    @property
    def secrets(self) -> tuple[SecretValue, ...]:
        return self._secrets

    def with_value(self, value: Any) -> SecretRedactor:
        return self.from_value(
            value,
            secrets=self._secrets,
            max_depth=self.max_depth,
            max_items=self.max_items,
            max_string_chars=self.max_string_chars,
        )

    @classmethod
    def from_value(
        cls,
        value: Any,
        *,
        secrets: Iterable[SecretValue] = (),
        max_depth: int = 8,
        max_items: int = 100,
        max_string_chars: int = 1_000_000,
    ) -> SecretRedactor:
        discovered = list(secrets)
        cls._discover_value(value, path=(), output=discovered, depth=0)
        return cls(
            discovered,
            max_depth=max_depth,
            max_items=max_items,
            max_string_chars=max_string_chars,
        )

    def redact(
        self,
        value: Any,
        *,
        view: RedactionView | str,
    ) -> RedactionResult:
        normalized_view = RedactionView(view)
        try:
            redacted = self._redact_value(
                value,
                view=normalized_view,
                path=(),
                depth=0,
            )
        except Exception:
            return RedactionResult(
                value=REDACTION_FAILED,
                view=normalized_view,
                failed=True,
            )
        return RedactionResult(
            value=redacted,
            view=normalized_view,
            secret_sources=tuple(self._source_view(secret) for secret in self._secrets),
        )

    def redact_with_value(
        self,
        value: Any,
        *,
        view: RedactionView | str,
    ) -> RedactionResult:
        normalized_view = RedactionView(view)
        try:
            combined = self.with_value(value)
        except Exception:
            return RedactionResult(
                value=REDACTION_FAILED,
                view=normalized_view,
                failed=True,
            )
        return combined.redact(value, view=normalized_view)

    def _redact_value(
        self,
        value: Any,
        *,
        view: RedactionView,
        path: tuple[str, ...],
        depth: int,
    ) -> Any:
        if isinstance(value, SecretValue):
            return self._secret_view(value, view)
        if depth >= self.max_depth:
            return "<truncated>"
        if isinstance(value, Mapping):
            items = list(value.items())
            redacted: dict[str, Any] = {}
            for raw_key, child in items[: self.max_items]:
                raw_key_text = str(raw_key)
                key = self._redact_text(raw_key_text, view=view)
                key = self._unique_mapping_key(key, redacted)
                child_path = (*path, raw_key_text)
                if self._is_sensitive_field(raw_key_text):
                    secret = self._secret_for_field(child, path=child_path)
                    redacted[key] = (
                        self._secret_view(secret, view)
                        if secret is not None
                        else self._sensitive_container_view(
                            raw_key_text,
                            path=child_path,
                            view=view,
                        )
                    )
                else:
                    redacted[key] = self._redact_value(
                        child,
                        view=view,
                        path=child_path,
                        depth=depth + 1,
                    )
            if len(items) > self.max_items:
                redacted["<truncated>"] = f"{len(items) - self.max_items} fields"
            return redacted
        if isinstance(value, list | tuple):
            items = [
                self._redact_value(
                    child,
                    view=view,
                    path=(*path, str(index)),
                    depth=depth + 1,
                )
                for index, child in enumerate(value[: self.max_items])
            ]
            if len(value) > self.max_items:
                items.append(f"<truncated {len(value) - self.max_items} items>")
            return items
        if isinstance(value, str):
            return self._redact_text(value, view=view)
        if value is None or isinstance(value, bool | int | float):
            return value
        return self._redact_text(repr(value), view=view)

    @staticmethod
    def _unique_mapping_key(key: str, output: Mapping[str, Any]) -> str:
        if key not in output:
            return key
        index = 2
        while f"{key}#{index}" in output:
            index += 1
        return f"{key}#{index}"

    def _redact_text(self, text: str, *, view: RedactionView) -> str:
        redacted = text
        for secret in self._secrets:
            redacted = redacted.replace(
                secret.value,
                self._text_marker(secret, view),
            )
        if len(redacted) <= self.max_string_chars:
            return redacted
        omitted = len(redacted) - self.max_string_chars
        return f"{redacted[: self.max_string_chars]}...[truncated {omitted} chars]"

    @staticmethod
    def _source_view(secret: SecretValue) -> dict[str, str]:
        return {
            "name": secret.name,
            "source": secret.source,
            "fingerprint": secret.fingerprint,
        }

    @staticmethod
    def _secret_view(secret: SecretValue, view: RedactionView) -> Any:
        if view is RedactionView.MODEL:
            return _MODEL_REDACTED
        if view is RedactionView.OPERATOR:
            return f"<redacted:{secret.name}>"
        result: dict[str, Any] = {
            "redacted": True,
            "name": secret.name,
            "source": secret.source,
        }
        if view is RedactionView.DEBUG:
            result["fingerprint"] = secret.fingerprint
        return result

    @classmethod
    def _sensitive_container_view(
        cls,
        key: str,
        *,
        path: tuple[str, ...],
        view: RedactionView,
    ) -> Any:
        name = cls._normalize_name(key).upper() or "SECRET"
        if view is RedactionView.MODEL:
            return _MODEL_REDACTED
        if view is RedactionView.OPERATOR:
            return f"<redacted:{name}>"
        return {
            "redacted": True,
            "name": name,
            "source": f"field:{'.'.join(path)}",
        }

    @staticmethod
    def _text_marker(secret: SecretValue, view: RedactionView) -> str:
        if view is RedactionView.MODEL:
            return _MODEL_REDACTED
        if view is RedactionView.DEBUG:
            return f"<redacted:{secret.name}:{secret.fingerprint}>"
        return f"<redacted:{secret.name}>"

    def _secret_for_field(
        self,
        value: Any,
        *,
        path: tuple[str, ...],
    ) -> SecretValue | None:
        if isinstance(value, SecretValue):
            return value
        if not isinstance(value, str) or not value:
            return None
        source = f"field:{'.'.join(path)}"
        normalized = self._normalize_name(path[-1])
        if normalized in {"authorization", "proxy_authorization"}:
            credential = self._authorization_credential(value)
            if credential:
                source_secret = self._matching_secret(credential, source=source)
                if source_secret is not None:
                    return source_secret
        return self._matching_secret(value, source=source)

    def _matching_secret(
        self,
        value: str,
        *,
        source: str,
    ) -> SecretValue | None:
        return next(
            (
                secret
                for secret in self._secrets
                if secret.value == value and secret.source == source
            ),
            next((secret for secret in self._secrets if secret.value == value), None),
        )

    @classmethod
    def _discover_value(
        cls,
        value: Any,
        *,
        path: tuple[str, ...],
        output: list[SecretValue],
        depth: int,
    ) -> None:
        if depth >= 12:
            return
        if isinstance(value, SecretValue):
            output.append(value)
            return
        if isinstance(value, Mapping):
            if cls._is_redaction_marker(value):
                return
            for raw_key, child in list(value.items())[:500]:
                key = str(raw_key)
                child_path = (*path, key)
                cls._discover_text_secrets(
                    key,
                    path=(*path, "<key>"),
                    output=output,
                )
                if cls._is_sensitive_field(key):
                    cls._discover_sensitive_field(
                        child,
                        path=child_path,
                        output=output,
                    )
                cls._discover_value(
                    child,
                    path=child_path,
                    output=output,
                    depth=depth + 1,
                )
            return
        if isinstance(value, list | tuple):
            for index, child in enumerate(value[:500]):
                cls._discover_value(
                    child,
                    path=(*path, str(index)),
                    output=output,
                    depth=depth + 1,
                )
            return
        if isinstance(value, str):
            cls._discover_text_secrets(value, path=path, output=output)

    @classmethod
    def _discover_sensitive_field(
        cls,
        value: Any,
        *,
        path: tuple[str, ...],
        output: list[SecretValue],
    ) -> None:
        if isinstance(value, SecretValue):
            output.append(value)
            return
        if isinstance(value, Mapping) and cls._is_redaction_marker(value):
            return
        if not isinstance(value, str) or not value:
            if isinstance(value, Mapping | list | tuple):
                cls._discover_sensitive_container(
                    value,
                    path=path,
                    name=cls._normalize_name(path[-1]).upper() or "SECRET",
                    output=output,
                    depth=0,
                )
            return
        source = f"field:{'.'.join(path)}"
        field_name = cls._normalize_name(path[-1])
        if field_name in {"authorization", "proxy_authorization"}:
            credential = cls._authorization_credential(value)
            if credential:
                output.append(
                    SecretValue(
                        value=credential,
                        name=field_name.upper(),
                        source=source,
                    )
                )
                return
        output.append(
            SecretValue(
                value=value,
                name=field_name.upper(),
                source=source,
            )
        )

    @classmethod
    def _discover_sensitive_container(
        cls,
        value: Any,
        *,
        path: tuple[str, ...],
        name: str,
        output: list[SecretValue],
        depth: int,
    ) -> None:
        if depth >= 12:
            return
        if isinstance(value, SecretValue):
            output.append(value)
            return
        if isinstance(value, str):
            if value:
                output.append(
                    SecretValue(
                        value=value,
                        name=name,
                        source=f"field:{'.'.join(path)}",
                    )
                )
            return
        if isinstance(value, Mapping):
            for raw_key, child in list(value.items())[:500]:
                key = str(raw_key)
                if key:
                    output.append(
                        SecretValue(
                            value=key,
                            name=name,
                            source=f"field:{'.'.join((*path, '<key>'))}",
                        )
                    )
                cls._discover_sensitive_container(
                    child,
                    path=(*path, key),
                    name=name,
                    output=output,
                    depth=depth + 1,
                )
            return
        if isinstance(value, list | tuple):
            for index, child in enumerate(value[:500]):
                cls._discover_sensitive_container(
                    child,
                    path=(*path, str(index)),
                    name=name,
                    output=output,
                    depth=depth + 1,
                )

    @classmethod
    def _discover_text_secrets(
        cls,
        text: str,
        *,
        path: tuple[str, ...],
        output: list[SecretValue],
    ) -> None:
        label = ".".join(path) or "text"
        cls._discover_url_secrets(text, label=label, output=output)
        for index, match in enumerate(_COMMAND_OPTION_RE.finditer(text)):
            value = next((group for group in match.groups() if group), "")
            if value:
                output.append(
                    SecretValue(
                        value=value,
                        name="COMMAND_SECRET",
                        source=f"command:{label}.option.{index}",
                    )
                )
        for match in _ASSIGNMENT_RE.finditer(text):
            value = next((group for group in match.groups()[1:] if group), "")
            if value:
                name = cls._normalize_name(match.group(1)).upper()
                output.append(
                    SecretValue(
                        value=value,
                        name=name,
                        source=f"text:{label}.{cls._normalize_name(match.group(1))}",
                    )
                )
        for pattern, name in (
            (_AUTHORIZATION_RE, "AUTHORIZATION"),
            (_SECRET_HEADER_RE, "SECRET_HEADER"),
        ):
            for index, match in enumerate(pattern.finditer(text)):
                value = match.group(1)
                if value:
                    output.append(
                        SecretValue(
                            value=value,
                            name=name,
                            source=f"header:{label}.{index}",
                        )
                    )

    @classmethod
    def _discover_url_secrets(
        cls,
        text: str,
        *,
        label: str,
        output: list[SecretValue],
    ) -> None:
        for candidate in re.findall(r"(?:https?|wss?|ftp)://[^\s\"']+", text):
            trimmed = candidate.rstrip(")]},.;")
            try:
                parsed = urlsplit(trimmed)
            except ValueError:
                continue
            if parsed.password:
                output.append(
                    SecretValue(
                        value=parsed.password,
                        name="URL_PASSWORD",
                        source=f"url:{label}.userinfo",
                    )
                )
            elif parsed.username and len(parsed.username) >= 8:
                output.append(
                    SecretValue(
                        value=parsed.username,
                        name="URL_USERINFO",
                        source=f"url:{label}.userinfo",
                    )
                )
            try:
                query_items = parse_qsl(parsed.query, keep_blank_values=True)
            except ValueError:
                query_items = []
            for raw_name, raw_value in query_items:
                normalized_name = cls._normalize_name(raw_name)
                if raw_value and normalized_name in _SENSITIVE_QUERY_NAMES:
                    output.append(
                        SecretValue(
                            value=raw_value,
                            name=normalized_name.upper(),
                            source=f"url:{label}.query.{normalized_name}",
                        )
                    )

    @staticmethod
    def _authorization_credential(value: str) -> str | None:
        match = re.fullmatch(
            r"\s*(?:[A-Za-z][\w.+-]*\s+)?([^\s\"']+)\s*",
            value,
        )
        return match.group(1) if match else None

    @classmethod
    def _is_sensitive_field(cls, key: str) -> bool:
        normalized = cls._normalize_name(key)
        return normalized in _SENSITIVE_FIELD_NAMES or normalized.endswith(
            (
                "_access_token",
                "_access_key",
                "_api_key",
                "_auth_token",
                "_client_secret",
                "_credential",
                "_password",
                "_private_key",
                "_refresh_token",
                "_secret",
                "_secret_key",
                "_token",
            )
        )

    @staticmethod
    def _normalize_name(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")

    @staticmethod
    def _is_redaction_marker(value: Mapping[Any, Any]) -> bool:
        return (
            value.get("redacted") is True
            and isinstance(value.get("name"), str)
            and isinstance(value.get("source"), str)
        )


def redact_tool_result(
    result: ToolResult,
    *,
    redactor: SecretRedactor,
    view: RedactionView | str,
) -> tuple[ToolResult, bool]:
    payload = {
        "result": {
            "content": result.content,
            "data": result.data,
            "is_error": result.is_error,
            "terminate": result.terminate,
            "model_output": result.model_output,
            "display_output": result.display_output,
        }
    }
    redacted = redactor.redact_with_value(
        payload,
        view=RedactionView(view),
    )
    if redacted.failed or not isinstance(redacted.value, Mapping):
        return (
            ToolResult(
                content=REDACTION_FAILED,
                data={
                    "executionStarted": bool(
                        isinstance(result.data, Mapping)
                        and result.data.get("executionStarted", False)
                    ),
                    "redactionFailed": True,
                },
                is_error=True,
                model_output=REDACTION_FAILED,
                display_output=REDACTION_FAILED,
            ),
            True,
        )
    result_payload = redacted.value.get("result")
    if not isinstance(result_payload, Mapping):
        return (
            ToolResult(
                content=REDACTION_FAILED,
                data={"executionStarted": False, "redactionFailed": True},
                is_error=True,
                model_output=REDACTION_FAILED,
                display_output=REDACTION_FAILED,
            ),
            True,
        )
    return (
        ToolResult(
            content=str(result_payload.get("content") or ""),
            data=result_payload.get("data"),
            is_error=bool(result_payload.get("is_error", False)),
            terminate=bool(result_payload.get("terminate", False)),
            model_output=(
                str(result_payload["model_output"])
                if result_payload.get("model_output") is not None
                else None
            ),
            display_output=(
                str(result_payload["display_output"])
                if result_payload.get("display_output") is not None
                else None
            ),
        ),
        False,
    )


def redact_exception_message(
    exc: BaseException,
    *,
    view: RedactionView | str = RedactionView.EVENT,
    context: Any = None,
    secrets: Iterable[SecretValue] = (),
    limit: int = 500,
) -> str:
    if limit < 1:
        raise ValueError("exception redaction limit must be positive")
    try:
        message = str(exc).replace("\r", " ").replace("\n", " ").strip()
    except Exception:
        message = REDACTION_FAILED
    payload = {
        "context": context,
        "exception": message,
    }
    result = SecretRedactor(
        secrets,
        max_string_chars=limit,
    ).redact_with_value(payload, view=RedactionView(view))
    rendered = (
        result.value.get("exception")
        if isinstance(result.value, Mapping)
        else None
    )
    if not isinstance(rendered, str):
        rendered = REDACTION_FAILED
    if len(rendered) > limit:
        rendered = f"{rendered[:limit]}... [truncated]"
    return rendered


def redact_exception(
    exc: BaseException,
    *,
    view: RedactionView | str = RedactionView.EVENT,
    context: Any = None,
    secrets: Iterable[SecretValue] = (),
    limit: int = 500,
) -> str:
    rendered = redact_exception_message(
        exc,
        view=view,
        context=context,
        secrets=secrets,
        limit=limit,
    )
    name = type(exc).__name__
    return f"{name}: {rendered}" if rendered else name
