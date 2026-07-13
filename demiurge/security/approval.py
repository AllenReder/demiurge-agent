from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol

from demiurge.runtime.scope import PrincipalScope
from demiurge.security.capabilities import CapabilitySnapshot
from demiurge.security.redaction import (
    REDACTION_FAILED,
    RedactionView,
    SecretRedactor,
)


ApprovalEventEmitter = Callable[..., dict[str, Any]]
_SENSITIVE_APPROVAL_KEY_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "credential",
    "password",
    "passwd",
    "private_key",
    "secret",
    "token",
)
_APPROVAL_PREVIEW_MAX_DEPTH = 6
_APPROVAL_PREVIEW_MAX_ITEMS = 50
_APPROVAL_PREVIEW_MAX_STRING = 500
_APPROVAL_SUMMARY_MAX_STRING = 500
_APPROVAL_TARGET_MAX_STRING = 1000
_APPROVAL_COMMAND_MAX_STRING = 2000
_SENSITIVE_COMMAND_NAME = (
    r"(?:api[-_]?key|authorization|cookie|credential|password|passwd|"
    r"private[-_]?key|secret|token)"
)
_SECRET_BINDING_METADATA_FIELDS = {
    "source",
    "target",
    "capability",
    "expires_at",
}


def _redact_approval_command(command: str | None) -> str | None:
    if command is None:
        return None
    redacted = re.sub(
        rf"(?i)(--{_SENSITIVE_COMMAND_NAME}(?:=|\s+))(?:\"[^\"]*\"|'[^']*'|\S+)",
        r"\1<redacted>",
        command,
    )
    redacted = re.sub(
        rf"(?i)(\b{_SENSITIVE_COMMAND_NAME}\s*=\s*)(?:\"[^\"]*\"|'[^']*'|\S+)",
        r"\1<redacted>",
        redacted,
    )
    redacted = re.sub(
        r"(?i)(authorization:\s*(?:bearer|basic)?\s*)(?:[^\s'\"]+)",
        r"\1<redacted>",
        redacted,
    )
    return redacted


def _redact_approval_text(value: str | None) -> str | None:
    if value is None:
        return None
    return re.sub(
        rf"(?i)(\b{_SENSITIVE_COMMAND_NAME}\s*=\s*)(?:\"[^\"]*\"|'[^']*'|[^\s&]+)",
        r"\1<redacted>",
        value,
    )


def _truncate_approval_text(value: str | None, *, limit: int) -> str | None:
    if value is None or len(value) <= limit:
        return value
    return f"{value[:limit]}...[truncated {len(value) - limit} chars]"


def _redact_secret_binding_metadata(value: Any, *, depth: int) -> Any:
    if not isinstance(value, list | tuple):
        return "<redacted>"
    bindings: list[Any] = []
    for item in value[:_APPROVAL_PREVIEW_MAX_ITEMS]:
        if not isinstance(item, Mapping):
            bindings.append("<redacted>")
            continue
        bindings.append(
            {
                str(item_key): (
                    _redact_approval_value(
                        item_value,
                        key=str(item_key),
                        depth=depth + 1,
                    )
                    if str(item_key) in _SECRET_BINDING_METADATA_FIELDS
                    else "<redacted>"
                )
                for item_key, item_value in list(item.items())[
                    :_APPROVAL_PREVIEW_MAX_ITEMS
                ]
            }
        )
    if len(value) > _APPROVAL_PREVIEW_MAX_ITEMS:
        bindings.append(
            f"<truncated {len(value) - _APPROVAL_PREVIEW_MAX_ITEMS} items>"
        )
    return bindings


def _redact_approval_value(value: Any, *, key: str = "", depth: int = 0) -> Any:
    normalized_key = key.strip().lower().replace("-", "_")
    if normalized_key == "secret_bindings":
        return _redact_secret_binding_metadata(value, depth=depth)
    if normalized_key in {"cmd", "command", "shell_command"} and isinstance(
        value, str
    ):
        return _truncate_approval_text(
            _redact_approval_command(value),
            limit=_APPROVAL_PREVIEW_MAX_STRING,
        )
    if normalized_key in {"summary", "target", "url"} and isinstance(value, str):
        return _truncate_approval_text(
            _redact_approval_text(value),
            limit=_APPROVAL_PREVIEW_MAX_STRING,
        )
    if normalized_key and any(
        part in normalized_key for part in _SENSITIVE_APPROVAL_KEY_PARTS
    ):
        return "<redacted>"
    if depth >= _APPROVAL_PREVIEW_MAX_DEPTH:
        return "<truncated>"
    if isinstance(value, Mapping):
        items = list(value.items())
        redacted = {
            str(item_key): _redact_approval_value(
                item_value,
                key=str(item_key),
                depth=depth + 1,
            )
            for item_key, item_value in items[:_APPROVAL_PREVIEW_MAX_ITEMS]
        }
        if len(items) > _APPROVAL_PREVIEW_MAX_ITEMS:
            redacted["<truncated>"] = (
                f"{len(items) - _APPROVAL_PREVIEW_MAX_ITEMS} fields"
            )
        return redacted
    if isinstance(value, list | tuple):
        items = [
            _redact_approval_value(item, depth=depth + 1)
            for item in value[:_APPROVAL_PREVIEW_MAX_ITEMS]
        ]
        if len(value) > _APPROVAL_PREVIEW_MAX_ITEMS:
            items.append(
                f"<truncated {len(value) - _APPROVAL_PREVIEW_MAX_ITEMS} items>"
            )
        return items
    if isinstance(value, str) and len(value) > _APPROVAL_PREVIEW_MAX_STRING:
        omitted = len(value) - _APPROVAL_PREVIEW_MAX_STRING
        return f"{value[:_APPROVAL_PREVIEW_MAX_STRING]}...[truncated {omitted} chars]"
    if value is None or isinstance(value, bool | int | float | str):
        return value
    return repr(value)[:_APPROVAL_PREVIEW_MAX_STRING]


def _structured_approval_redaction(value: Any) -> Any:
    result = SecretRedactor(
        max_depth=_APPROVAL_PREVIEW_MAX_DEPTH,
        max_items=_APPROVAL_PREVIEW_MAX_ITEMS,
        max_string_chars=max(
            _APPROVAL_COMMAND_MAX_STRING,
            _APPROVAL_TARGET_MAX_STRING,
            _APPROVAL_SUMMARY_MAX_STRING,
        ),
    ).redact_with_value(value, view=RedactionView.MODEL)
    return result.value if not result.failed else REDACTION_FAILED


@dataclass(frozen=True, slots=True)
class ApprovalDecision:
    value: str
    reason: str = ""

    @property
    def allowed(self) -> bool:
        return self.value in {"allow", "always_allow_for_session"}


@dataclass(frozen=True, slots=True)
class ApprovalScope:
    principal_scope: PrincipalScope = field(repr=False)
    turn_id: str
    core_id: str
    core_revision: str
    capability_snapshot: CapabilitySnapshot = field(repr=False)

    @property
    def principal_id(self) -> str:
        return self.principal_scope.principal_id

    @property
    def session_id(self) -> str:
        return self.principal_scope.session_id

    @classmethod
    def from_execution_context(cls, context: Any) -> "ApprovalScope":
        if context.principal_scope.session_id != context.session_id:
            raise ValueError("TurnExecutionContext principal/session mismatch")
        turn_id = context.cancellation.turn_id
        if (
            context.admission_lease.turn_id != turn_id
            or context.admission_lease.session_id != context.session_id
            or getattr(context, "trace_id", turn_id) != turn_id
        ):
            raise ValueError("TurnExecutionContext approval correlation mismatch")
        return cls(
            principal_scope=context.principal_scope,
            turn_id=turn_id,
            core_id=context.core_id,
            core_revision=context.core_revision,
            capability_snapshot=context.capability_snapshot,
        )

    @classmethod
    def for_host_operation(
        cls,
        *,
        principal_scope: PrincipalScope,
        turn_id: str,
        core_id: str,
        core_revision: str,
        capability_snapshot: CapabilitySnapshot,
    ) -> "ApprovalScope":
        if not turn_id:
            raise ValueError("approval turn_id is required")
        return cls(
            principal_scope=principal_scope,
            turn_id=turn_id,
            core_id=core_id,
            core_revision=core_revision,
            capability_snapshot=capability_snapshot,
        )


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    scope: ApprovalScope = field(repr=False)
    tool_name: str
    tool_call_id: str
    capability: str
    action: str
    risk: str
    summary: str
    target: str | None = None
    command: str | None = None
    arguments_preview: dict[str, Any] = field(default_factory=dict)
    cache_key: str | None = None
    auto_approve: bool = False
    policy: str = "prompt"
    session_cacheable: bool = True

    def __post_init__(self) -> None:
        structured = _structured_approval_redaction(
            {
                "arguments_preview": self.arguments_preview,
                "command": self.command,
                "summary": self.summary,
                "target": self.target,
            }
        )
        if not isinstance(structured, Mapping):
            structured = {
                "arguments_preview": REDACTION_FAILED,
                "command": REDACTION_FAILED,
                "summary": REDACTION_FAILED,
                "target": REDACTION_FAILED,
            }
        structured_preview = structured.get("arguments_preview")
        preview = (
            _redact_approval_value(structured_preview)
            if structured_preview != REDACTION_FAILED
            else {"redaction": REDACTION_FAILED}
        )
        if not isinstance(preview, dict):
            preview = {"redaction": REDACTION_FAILED}
        object.__setattr__(
            self,
            "arguments_preview",
            preview,
        )
        structured_command = structured.get("command")
        object.__setattr__(
            self,
            "command",
            (
                REDACTION_FAILED
                if structured_command == REDACTION_FAILED
                else _truncate_approval_text(
                    _redact_approval_command(structured_command),
                    limit=_APPROVAL_COMMAND_MAX_STRING,
                )
            ),
        )
        structured_summary = structured.get("summary")
        object.__setattr__(
            self,
            "summary",
            (
                REDACTION_FAILED
                if structured_summary == REDACTION_FAILED
                else _truncate_approval_text(
                    _redact_approval_text(structured_summary),
                    limit=_APPROVAL_SUMMARY_MAX_STRING,
                )
            ),
        )
        structured_target = structured.get("target")
        object.__setattr__(
            self,
            "target",
            (
                REDACTION_FAILED
                if structured_target == REDACTION_FAILED
                else _truncate_approval_text(
                    _redact_approval_text(structured_target),
                    limit=_APPROVAL_TARGET_MAX_STRING,
                )
            ),
        )

    @property
    def principal_id(self) -> str:
        return self.scope.principal_id

    @property
    def session_id(self) -> str:
        return self.scope.session_id

    @property
    def turn_id(self) -> str:
        return self.scope.turn_id

    @property
    def core_id(self) -> str:
        return self.scope.core_id

    @property
    def core_revision(self) -> str:
        return self.scope.core_revision

    @property
    def policy_fingerprint(self) -> str:
        snapshot = self.scope.capability_snapshot
        payload = {
            "core_id": self.core_id,
            "core_revision": self.core_revision,
            "capabilities": {
                "defaults": sorted(snapshot.defaults),
                "manifest_slots": [
                    [slot_path, sorted(capabilities)]
                    for slot_path, capabilities in snapshot.manifest_slots
                ],
                "component_slots": [
                    [slot_path, sorted(capabilities)]
                    for slot_path, capabilities in snapshot.component_slots
                ],
            },
            "effect_entry": {
                "tool_name": self.tool_name,
                "capability": self.capability,
                "action": self.action,
                "risk": self.risk,
            },
            "effective_policy": self.policy,
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def redacted_view(self) -> dict[str, Any]:
        cache_key_fingerprint = None
        if self.cache_key is not None:
            cache_key_fingerprint = hashlib.sha256(
                self.cache_key.encode("utf-8")
            ).hexdigest()[:16]
        return {
            "tool_name": self.tool_name,
            "tool_call_id": self.tool_call_id,
            "turn_id": self.turn_id,
            "session_id": self.session_id,
            "principal": self.scope.principal_scope.redacted_view(),
            "core_id": self.core_id,
            "core_revision": self.core_revision,
            "policy_fingerprint": self.policy_fingerprint,
            "capability": self.capability,
            "action": self.action,
            "risk": self.risk,
            "summary": self.summary,
            "target": self.target,
            "command": self.command,
            "arguments_preview": dict(self.arguments_preview),
            "cache_key_fingerprint": cache_key_fingerprint,
            "policy": self.policy,
            "session_cacheable": self.session_cacheable,
        }


class ApprovalProvider(Protocol):
    name: str

    def decide(self, request: ApprovalRequest) -> ApprovalDecision:
        ...


class AutoDenyApprovalProvider:
    name = "auto_deny"

    def decide(self, request: ApprovalRequest) -> ApprovalDecision:
        return ApprovalDecision("deny", "no interactive approval provider configured")


class StaticApprovalProvider:
    def __init__(self, decision: str = "allow", *, reason: str = "test approval"):
        self.name = f"static_{decision}"
        self.decision = decision
        self.reason = reason

    def decide(self, request: ApprovalRequest) -> ApprovalDecision:
        return ApprovalDecision(self.decision, self.reason)


@dataclass(slots=True)
class _ApprovalKeyLock:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    users: int = 0


@dataclass(frozen=True, slots=True)
class _CachedAllow:
    core_id: str
    expires_at: float


@dataclass(frozen=True, slots=True)
class _ApprovalCacheKey:
    principal_id: str
    session_id: str
    core_id: str
    policy_fingerprint: str
    rule_key: str


class ApprovalRuntime:
    def __init__(
        self,
        provider: ApprovalProvider | None = None,
        *,
        session_allow_ttl_seconds: float = 8 * 60 * 60,
        clock: Callable[[], float] = time.monotonic,
    ):
        if session_allow_ttl_seconds <= 0:
            raise ValueError("session allow TTL must be positive")
        self.provider = provider or AutoDenyApprovalProvider()
        self._session_allow_ttl_seconds = float(session_allow_ttl_seconds)
        self._clock = clock
        self._session_allowlist: dict[
            _ApprovalCacheKey,
            _CachedAllow,
        ] = {}
        self._decision_locks: dict[_ApprovalCacheKey, _ApprovalKeyLock] = {}
        self._global_generation = 0
        self._principal_generations: dict[str, int] = {}
        self._session_generations: dict[str, int] = {}
        self._core_generations: dict[str, int] = {}
        self._closed = False

    @property
    def mode(self) -> str:
        return self.provider.name

    @property
    def cached_allow_count(self) -> int:
        self._remove_expired()
        return len(self._session_allowlist)

    @property
    def pending_decision_count(self) -> int:
        return sum(entry.users for entry in self._decision_locks.values())

    def invalidate_session(self, session_id: str) -> int:
        if any(
            cache_key.session_id == session_id
            for cache_key in self._decision_locks
        ):
            self._session_generations[session_id] = (
                self._session_generations.get(session_id, 0) + 1
            )
        else:
            self._session_generations.pop(session_id, None)
        matching = [
            cache_key
            for cache_key in self._session_allowlist
            if cache_key.session_id == session_id
        ]
        for cache_key in matching:
            self._session_allowlist.pop(cache_key, None)
        return len(matching)

    def revoke_principal(self, principal_id: str) -> int:
        if any(
            cache_key.principal_id == principal_id
            for cache_key in self._decision_locks
        ):
            self._principal_generations[principal_id] = (
                self._principal_generations.get(principal_id, 0) + 1
            )
        else:
            self._principal_generations.pop(principal_id, None)
        matching = [
            cache_key
            for cache_key in self._session_allowlist
            if cache_key.principal_id == principal_id
        ]
        for cache_key in matching:
            self._session_allowlist.pop(cache_key, None)
        return len(matching)

    def clear(self) -> int:
        self._global_generation += 1
        removed = len(self._session_allowlist)
        self._session_allowlist.clear()
        return removed

    def close(self) -> int:
        self._closed = True
        return self.clear()

    def invalidate_core(self, core_id: str) -> int:
        if any(
            cache_key.core_id == core_id
            for cache_key in self._decision_locks
        ):
            self._core_generations[core_id] = (
                self._core_generations.get(core_id, 0) + 1
            )
        else:
            self._core_generations.pop(core_id, None)
        matching = [
            cache_key
            for cache_key, cached in self._session_allowlist.items()
            if cached.core_id == core_id
        ]
        for cache_key in matching:
            self._session_allowlist.pop(cache_key, None)
        return len(matching)

    def _owner_generation(self, request: ApprovalRequest) -> tuple[int, int, int, int]:
        return (
            self._global_generation,
            self._principal_generations.get(request.principal_id, 0),
            self._session_generations.get(request.session_id, 0),
            self._core_generations.get(request.core_id, 0),
        )

    def _remove_expired(self) -> None:
        now = self._clock()
        expired = [
            cache_key
            for cache_key, cached in self._session_allowlist.items()
            if cached.expires_at <= now
        ]
        for cache_key in expired:
            self._session_allowlist.pop(cache_key, None)

    def _is_cached(self, cache_key: _ApprovalCacheKey) -> bool:
        cached = self._session_allowlist.get(cache_key)
        if cached is None:
            return False
        if cached.expires_at <= self._clock():
            self._session_allowlist.pop(cache_key, None)
            return False
        return True

    async def decide(
        self,
        request: ApprovalRequest,
        *,
        emit_event: ApprovalEventEmitter | None = None,
    ) -> ApprovalDecision:
        if self._closed:
            return self._closed_decision(request, emit_event=emit_event)
        rule_key = request.cache_key or f"{request.tool_name}:{request.capability}:{request.action}"
        cache_key = _ApprovalCacheKey(
            principal_id=request.principal_id,
            session_id=request.session_id,
            core_id=request.core_id,
            policy_fingerprint=request.policy_fingerprint,
            rule_key=rule_key,
        )
        if request.policy == "deny":
            decision = ApprovalDecision("deny", "denied by approval policy")
            self._emit_decided(request, decision, emit_event=emit_event, cached=False, automatic=True)
            if emit_event:
                emit_event("approval.denied", **self._event_payload(request), reason=decision.reason)
            return decision
        if request.auto_approve or request.policy == "auto":
            decision = ApprovalDecision("allow", "auto-approved by host policy")
            self._emit_decided(request, decision, emit_event=emit_event, cached=False, automatic=True)
            return decision
        admission_generation = self._owner_generation(request)
        key_lock = self._decision_locks.setdefault(cache_key, _ApprovalKeyLock())
        key_lock.users += 1
        acquired = False
        try:
            await key_lock.lock.acquire()
            acquired = True
            if self._closed:
                return self._closed_decision(request, emit_event=emit_event)
            if self._owner_generation(request) != admission_generation:
                return self._invalidated_decision(
                    request,
                    emit_event=emit_event,
                    reason="approval scope invalidated while waiting for decision admission",
                )
            if request.session_cacheable and self._is_cached(cache_key):
                decision = ApprovalDecision("allow", "session allowlist")
                self._emit_decided(request, decision, emit_event=emit_event, cached=True, automatic=False)
                return decision

            if emit_event:
                emit_event("approval.requested", **self._event_payload(request))

            value = self.provider.decide(request)
            if inspect.isawaitable(value):
                value = await value
            decision = self._normalize_decision(value)
            if (
                decision.value == "always_allow_for_session"
                and not request.session_cacheable
            ):
                decision = ApprovalDecision(
                    "allow",
                    "approved once; session caching is disabled for this request",
                )
            if self._owner_generation(request) != admission_generation:
                decision = self._invalidated_decision(
                    request,
                    emit_event=None,
                    reason="approval scope invalidated while decision was pending",
                )
            if decision.value == "always_allow_for_session" and request.session_cacheable:
                self._session_allowlist[cache_key] = _CachedAllow(
                    core_id=request.core_id,
                    expires_at=self._clock() + self._session_allow_ttl_seconds,
                )
            self._emit_decided(request, decision, emit_event=emit_event, cached=False, automatic=False)
            if not decision.allowed and emit_event:
                emit_event("approval.denied", **self._event_payload(request), reason=decision.reason)
            return decision
        finally:
            if acquired:
                key_lock.lock.release()
            key_lock.users -= 1
            if key_lock.users == 0 and self._decision_locks.get(cache_key) is key_lock:
                self._decision_locks.pop(cache_key, None)
                self._prune_generation_tombstones(cache_key)

    def _prune_generation_tombstones(self, cache_key: _ApprovalCacheKey) -> None:
        if not any(
            pending.principal_id == cache_key.principal_id
            for pending in self._decision_locks
        ):
            self._principal_generations.pop(cache_key.principal_id, None)
        if not any(
            pending.session_id == cache_key.session_id
            for pending in self._decision_locks
        ):
            self._session_generations.pop(cache_key.session_id, None)
        if not any(
            pending.core_id == cache_key.core_id
            for pending in self._decision_locks
        ):
            self._core_generations.pop(cache_key.core_id, None)

    def _closed_decision(
        self,
        request: ApprovalRequest,
        *,
        emit_event: ApprovalEventEmitter | None,
    ) -> ApprovalDecision:
        decision = ApprovalDecision("deny", "approval runtime is closed")
        self._emit_decided(
            request,
            decision,
            emit_event=emit_event,
            cached=False,
            automatic=True,
        )
        if emit_event:
            emit_event(
                "approval.denied",
                **self._event_payload(request),
                reason=decision.reason,
            )
        return decision

    def _invalidated_decision(
        self,
        request: ApprovalRequest,
        *,
        emit_event: ApprovalEventEmitter | None,
        reason: str,
    ) -> ApprovalDecision:
        decision = ApprovalDecision("deny", reason)
        self._emit_decided(
            request,
            decision,
            emit_event=emit_event,
            cached=False,
            automatic=True,
        )
        if emit_event:
            emit_event(
                "approval.denied",
                **self._event_payload(request),
                reason=decision.reason,
            )
        return decision

    def _normalize_decision(self, value: ApprovalDecision | str) -> ApprovalDecision:
        if isinstance(value, ApprovalDecision):
            decision = value
        else:
            decision = ApprovalDecision(str(value))
        if decision.value not in {"allow", "deny", "always_allow_for_session"}:
            return ApprovalDecision("deny", f"invalid approval decision: {decision.value}")
        return decision

    def _emit_decided(
        self,
        request: ApprovalRequest,
        decision: ApprovalDecision,
        *,
        emit_event: ApprovalEventEmitter | None,
        cached: bool,
        automatic: bool,
    ) -> None:
        if not emit_event:
            return
        emit_event(
            "approval.decided",
            **self._event_payload(request),
            decision=decision.value,
            reason=decision.reason,
            cached=cached,
            automatic=automatic,
        )

    def _event_payload(self, request: ApprovalRequest) -> dict[str, Any]:
        return request.redacted_view()
