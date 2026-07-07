from __future__ import annotations

import re
from urllib.parse import quote


_KEY_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def build_conversation_key(channel: str, scope: str, *ids: object, thread_id: object | None = None) -> str:
    """Build a stable host-owned route key for external channel conversations."""

    channel_name = _validate_key_name(channel, "channel")
    scope_name = _validate_key_name(scope, "scope")
    encoded_ids = [_encode_required_id(value) for value in ids]
    if not encoded_ids:
        raise ValueError("conversation key requires at least one id")
    parts = [channel_name, scope_name, *encoded_ids]
    encoded_thread_id = _encode_optional_id(thread_id)
    if encoded_thread_id is not None:
        parts.extend(["thread", encoded_thread_id])
    return ":".join(parts)


def _validate_key_name(value: str, label: str) -> str:
    text = str(value or "")
    if not _KEY_NAME_RE.fullmatch(text):
        raise ValueError(f"conversation key {label} must be a lowercase identifier")
    return text


def _encode_required_id(value: object) -> str:
    encoded = _encode_optional_id(value)
    if encoded is None:
        raise ValueError("conversation key id must not be empty")
    return encoded


def _encode_optional_id(value: object | None) -> str | None:
    if value is None:
        return None
    text = str(value)
    if not text:
        return None
    return quote(text, safe="")
