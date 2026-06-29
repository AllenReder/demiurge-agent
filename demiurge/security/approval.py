from __future__ import annotations

import inspect
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Protocol


ApprovalEventEmitter = Callable[..., dict[str, Any]]


@dataclass(frozen=True, slots=True)
class ApprovalDecision:
    value: str
    reason: str = ""

    @property
    def allowed(self) -> bool:
        return self.value in {"allow", "always_allow_for_session"}


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    tool_name: str
    tool_call_id: str
    turn_id: str
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


class ApprovalRuntime:
    def __init__(self, provider: ApprovalProvider | None = None):
        self.provider = provider or AutoDenyApprovalProvider()
        self._session_allowlist: set[str] = set()

    @property
    def mode(self) -> str:
        return self.provider.name

    @property
    def cached_allow_count(self) -> int:
        return len(self._session_allowlist)

    async def decide(
        self,
        request: ApprovalRequest,
        *,
        emit_event: ApprovalEventEmitter | None = None,
    ) -> ApprovalDecision:
        cache_key = request.cache_key or f"{request.tool_name}:{request.capability}:{request.action}"
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
        if cache_key in self._session_allowlist:
            decision = ApprovalDecision("allow", "session allowlist")
            self._emit_decided(request, decision, emit_event=emit_event, cached=True, automatic=False)
            return decision

        if emit_event:
            emit_event("approval.requested", **self._event_payload(request))

        value = self.provider.decide(request)
        if inspect.isawaitable(value):
            value = await value
        decision = self._normalize_decision(value)
        if decision.value == "always_allow_for_session":
            self._session_allowlist.add(cache_key)
        self._emit_decided(request, decision, emit_event=emit_event, cached=False, automatic=False)
        if not decision.allowed and emit_event:
            emit_event("approval.denied", **self._event_payload(request), reason=decision.reason)
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
        payload = asdict(request)
        payload.pop("auto_approve", None)
        return payload
