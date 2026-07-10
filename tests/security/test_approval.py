import pytest

from demiurge.security.approval import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalRuntime,
    StaticApprovalProvider,
)


def _request(**overrides):
    data = {
        "tool_name": "write_file",
        "tool_call_id": "call_1",
        "turn_id": "turn_1",
        "capability": "fs.write",
        "action": "write",
        "risk": "high",
        "summary": "write note",
        "target": "note.txt",
        "cache_key": "write:note",
    }
    data.update(overrides)
    return ApprovalRequest(**data)


@pytest.mark.asyncio
async def test_auto_approval_records_decision_without_prompt():
    events = []
    runtime = ApprovalRuntime(StaticApprovalProvider("deny"))

    decision = await runtime.decide(
        _request(auto_approve=True),
        emit_event=lambda event_type, **data: events.append({"type": event_type, **data}) or events[-1],
    )

    assert decision.allowed is True
    assert [event["type"] for event in events] == ["approval.decided"]
    assert events[0]["automatic"] is True


@pytest.mark.asyncio
async def test_denied_approval_records_requested_decided_and_denied():
    events = []
    runtime = ApprovalRuntime(StaticApprovalProvider("deny", reason="no"))

    decision = await runtime.decide(
        _request(),
        emit_event=lambda event_type, **data: events.append({"type": event_type, **data}) or events[-1],
    )

    assert decision.allowed is False
    assert [event["type"] for event in events] == [
        "approval.requested",
        "approval.decided",
        "approval.denied",
    ]


@pytest.mark.asyncio
async def test_always_allow_for_session_caches_future_requests():
    events = []
    runtime = ApprovalRuntime(StaticApprovalProvider("always_allow_for_session"))

    first = await runtime.decide(
        _request(),
        emit_event=lambda event_type, **data: events.append({"type": event_type, **data}) or events[-1],
    )
    second = await runtime.decide(
        _request(),
        emit_event=lambda event_type, **data: events.append({"type": event_type, **data}) or events[-1],
    )

    assert first.allowed is True
    assert second.allowed is True
    assert runtime.cached_allow_count == 1
    assert events[-1]["cached"] is True


@pytest.mark.asyncio
async def test_auth_01_session_allow_cache_does_not_authorize_another_session():
    """AUTH-01: a session-scoped allow decision must not bleed into another session."""

    class SessionAwareProvider:
        name = "session-aware"

        def __init__(self):
            self.requests = []

        def decide(self, request):
            self.requests.append(request)
            if request.session_id == "session_A":
                return ApprovalDecision("always_allow_for_session")
            return ApprovalDecision("deny")

    provider = SessionAwareProvider()
    runtime = ApprovalRuntime(provider)

    first = await runtime.decide(_request(session_id="session_A"))
    second = await runtime.decide(_request(session_id="session_B"))

    assert {
        "first": first.value,
        "second": second.value,
        "provider_sessions": [request.session_id for request in provider.requests],
    } == {
        "first": "always_allow_for_session",
        "second": "deny",
        "provider_sessions": ["session_A", "session_B"],
    }
