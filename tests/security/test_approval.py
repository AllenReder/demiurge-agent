import asyncio
import json
from types import SimpleNamespace

import pytest

from demiurge.app import create_app
from demiurge.security.approval import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalRuntime,
    ApprovalScope,
    StaticApprovalProvider,
)
from demiurge.security.redaction import REDACTION_FAILED, SecretRedactor
from demiurge.security.capabilities import CapabilitySnapshot
from demiurge.runtime.scope import PrincipalScopeResolver
from demiurge.runtime.store import RuntimeStore


def _scope(
    tmp_path,
    *,
    principal_key="principal_A",
    session_id="session_A",
    core_revision="revision_1",
    capabilities=frozenset({"fs.write"}),
):
    principal_scope = PrincipalScopeResolver(
        RuntimeStore(tmp_path / f"{principal_key}_{session_id}.sqlite3")
    ).issue_conversation(
        channel="test",
        principal_key=principal_key,
        conversation_key=f"test:dm:{principal_key}",
        session_id=session_id,
    )
    return ApprovalScope.for_host_operation(
        principal_scope=principal_scope,
        turn_id=f"turn_{session_id}",
        core_id="assistant",
        core_revision=core_revision,
        capability_snapshot=CapabilitySnapshot(
            defaults=capabilities,
            manifest_slots=(),
            component_slots=(),
        ),
    )


def _request(*, scope, **overrides):
    data = {
        "scope": scope,
        "tool_name": "write_file",
        "tool_call_id": "call_1",
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
async def test_auto_approval_records_decision_without_prompt(tmp_path):
    events = []
    runtime = ApprovalRuntime(StaticApprovalProvider("deny"))

    decision = await runtime.decide(
        _request(scope=_scope(tmp_path), auto_approve=True),
        emit_event=lambda event_type, **data: events.append({"type": event_type, **data}) or events[-1],
    )

    assert decision.allowed is True
    assert [event["type"] for event in events] == ["approval.decided"]
    assert events[0]["automatic"] is True


@pytest.mark.asyncio
async def test_denied_approval_records_requested_decided_and_denied(tmp_path):
    events = []
    runtime = ApprovalRuntime(StaticApprovalProvider("deny", reason="no"))

    decision = await runtime.decide(
        _request(scope=_scope(tmp_path)),
        emit_event=lambda event_type, **data: events.append({"type": event_type, **data}) or events[-1],
    )

    assert decision.allowed is False
    assert [event["type"] for event in events] == [
        "approval.requested",
        "approval.decided",
        "approval.denied",
    ]


@pytest.mark.asyncio
async def test_always_allow_for_session_caches_future_requests(tmp_path):
    events = []
    runtime = ApprovalRuntime(StaticApprovalProvider("always_allow_for_session"))

    first = await runtime.decide(
        _request(scope=_scope(tmp_path)),
        emit_event=lambda event_type, **data: events.append({"type": event_type, **data}) or events[-1],
    )
    second = await runtime.decide(
        _request(scope=_scope(tmp_path)),
        emit_event=lambda event_type, **data: events.append({"type": event_type, **data}) or events[-1],
    )

    assert first.allowed is True
    assert second.allowed is True
    assert runtime.cached_allow_count == 1
    assert events[-1]["cached"] is True


@pytest.mark.asyncio
async def test_auth_01_session_allow_cache_does_not_authorize_another_session(tmp_path):
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

    first = await runtime.decide(
        _request(scope=_scope(tmp_path, session_id="session_A"))
    )
    second = await runtime.decide(
        _request(scope=_scope(tmp_path, session_id="session_B"))
    )

    assert {
        "first": first.value,
        "second": second.value,
        "provider_sessions": [request.session_id for request in provider.requests],
    } == {
        "first": "always_allow_for_session",
        "second": "deny",
        "provider_sessions": ["session_A", "session_B"],
    }


@pytest.mark.asyncio
async def test_session_allow_cache_does_not_authorize_another_principal(tmp_path):
    class PrincipalAwareProvider:
        name = "principal-aware"

        def __init__(self):
            self.principals = []

        def decide(self, request):
            self.principals.append(request.principal_id)
            if len(self.principals) == 1:
                return ApprovalDecision("always_allow_for_session")
            return ApprovalDecision("deny")

    provider = PrincipalAwareProvider()
    runtime = ApprovalRuntime(provider)

    first = await runtime.decide(
        _request(
            scope=_scope(
                tmp_path,
                principal_key="principal_A",
                session_id="session_shared",
            )
        )
    )
    second = await runtime.decide(
        _request(
            scope=_scope(
                tmp_path,
                principal_key="principal_B",
                session_id="session_shared",
            )
        )
    )

    assert [first.value, second.value] == ["always_allow_for_session", "deny"]
    assert len(provider.principals) == 2
    assert provider.principals[0] != provider.principals[1]


@pytest.mark.asyncio
async def test_session_allow_cache_is_atomic_for_concurrent_matching_requests(tmp_path):
    class BlockingProvider:
        name = "blocking"

        def __init__(self):
            self.calls = 0
            self.first_started = asyncio.Event()
            self.release_first = asyncio.Event()

        async def decide(self, request):
            self.calls += 1
            if self.calls == 1:
                self.first_started.set()
                await self.release_first.wait()
            return ApprovalDecision("always_allow_for_session")

    provider = BlockingProvider()
    runtime = ApprovalRuntime(provider)
    request = _request(scope=_scope(tmp_path))

    first_task = asyncio.create_task(runtime.decide(request))
    await provider.first_started.wait()
    second_task = asyncio.create_task(runtime.decide(request))
    await asyncio.sleep(0)
    provider.release_first.set()

    first, second = await asyncio.gather(first_task, second_task)

    assert [first.value, second.value] == [
        "always_allow_for_session",
        "allow",
    ]
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_cancelled_cache_waiter_releases_decision_admission(tmp_path):
    class BlockingProvider:
        name = "blocking"

        def __init__(self):
            self.first_started = asyncio.Event()
            self.release_first = asyncio.Event()

        async def decide(self, request):
            self.first_started.set()
            await self.release_first.wait()
            return ApprovalDecision("always_allow_for_session")

    provider = BlockingProvider()
    runtime = ApprovalRuntime(provider)
    request = _request(scope=_scope(tmp_path))

    first_task = asyncio.create_task(runtime.decide(request))
    await provider.first_started.wait()
    waiting_task = asyncio.create_task(runtime.decide(request))
    await asyncio.sleep(0)
    waiting_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting_task

    provider.release_first.set()
    await first_task

    assert runtime.pending_decision_count == 0


@pytest.mark.asyncio
async def test_session_allow_cache_does_not_survive_core_revision_change(tmp_path):
    class RevisionProvider:
        name = "revision-aware"

        def __init__(self):
            self.revisions = []

        def decide(self, request):
            self.revisions.append(request.core_revision)
            if request.core_revision == "revision_1":
                return ApprovalDecision("always_allow_for_session")
            return ApprovalDecision("deny")

    provider = RevisionProvider()
    runtime = ApprovalRuntime(provider)

    first = await runtime.decide(
        _request(scope=_scope(tmp_path, core_revision="revision_1"))
    )
    second = await runtime.decide(
        _request(scope=_scope(tmp_path, core_revision="revision_2"))
    )

    assert [first.value, second.value] == ["always_allow_for_session", "deny"]
    assert provider.revisions == ["revision_1", "revision_2"]


@pytest.mark.asyncio
async def test_session_allow_cache_does_not_survive_capability_policy_change(tmp_path):
    class SequenceProvider:
        name = "sequence"

        def __init__(self):
            self.calls = 0

        def decide(self, request):
            self.calls += 1
            if self.calls == 1:
                return ApprovalDecision("always_allow_for_session")
            return ApprovalDecision("deny")

    provider = SequenceProvider()
    runtime = ApprovalRuntime(provider)

    first = await runtime.decide(
        _request(scope=_scope(tmp_path, capabilities=frozenset({"fs.write"})))
    )
    second = await runtime.decide(
        _request(
            scope=_scope(
                tmp_path,
                capabilities=frozenset({"fs.write", "network.fetch"}),
            )
        )
    )

    assert [first.value, second.value] == ["always_allow_for_session", "deny"]
    assert provider.calls == 2


@pytest.mark.asyncio
async def test_session_end_invalidates_cached_allows(tmp_path):
    class SequenceProvider:
        name = "sequence"

        def __init__(self):
            self.calls = 0

        def decide(self, request):
            self.calls += 1
            if self.calls == 1:
                return ApprovalDecision("always_allow_for_session")
            return ApprovalDecision("deny")

    provider = SequenceProvider()
    runtime = ApprovalRuntime(provider)
    request = _request(scope=_scope(tmp_path))

    first = await runtime.decide(request)
    removed = runtime.invalidate_session(request.session_id)
    second = await runtime.decide(request)

    assert first.value == "always_allow_for_session"
    assert removed == 1
    assert second.value == "deny"
    assert provider.calls == 2


@pytest.mark.asyncio
async def test_session_allow_cache_expires_after_bounded_ttl(tmp_path):
    now = [100.0]

    class SequenceProvider:
        name = "sequence"

        def __init__(self):
            self.calls = 0

        def decide(self, request):
            self.calls += 1
            if self.calls == 1:
                return ApprovalDecision("always_allow_for_session")
            return ApprovalDecision("deny")

    provider = SequenceProvider()
    runtime = ApprovalRuntime(
        provider,
        session_allow_ttl_seconds=10,
        clock=lambda: now[0],
    )
    request = _request(scope=_scope(tmp_path))

    first = await runtime.decide(request)
    now[0] += 11
    second = await runtime.decide(request)

    assert [first.value, second.value] == ["always_allow_for_session", "deny"]
    assert provider.calls == 2
    assert runtime.cached_allow_count == 0


@pytest.mark.asyncio
async def test_principal_revoke_prevents_pending_decision_from_repopulating_cache(tmp_path):
    class BlockingProvider:
        name = "blocking"

        def __init__(self):
            self.calls = 0
            self.first_started = asyncio.Event()
            self.release_first = asyncio.Event()

        async def decide(self, request):
            self.calls += 1
            if self.calls == 1:
                self.first_started.set()
                await self.release_first.wait()
                return ApprovalDecision("always_allow_for_session")
            return ApprovalDecision("deny")

    provider = BlockingProvider()
    runtime = ApprovalRuntime(provider)
    request = _request(scope=_scope(tmp_path))

    first_task = asyncio.create_task(runtime.decide(request))
    await provider.first_started.wait()
    assert runtime.revoke_principal(request.principal_id) == 0
    provider.release_first.set()

    first = await first_task
    second = await runtime.decide(request)

    assert [first.value, second.value] == ["deny", "deny"]
    assert provider.calls == 2
    assert runtime.cached_allow_count == 0


@pytest.mark.asyncio
async def test_principal_revoke_denies_waiters_admitted_before_revocation(tmp_path):
    class BlockingProvider:
        name = "blocking"

        def __init__(self):
            self.calls = 0
            self.first_started = asyncio.Event()
            self.release_first = asyncio.Event()

        async def decide(self, request):
            self.calls += 1
            if self.calls == 1:
                self.first_started.set()
                await self.release_first.wait()
            return ApprovalDecision("always_allow_for_session")

    provider = BlockingProvider()
    runtime = ApprovalRuntime(provider)
    request = _request(scope=_scope(tmp_path))

    first_task = asyncio.create_task(runtime.decide(request))
    await provider.first_started.wait()
    waiting_task = asyncio.create_task(runtime.decide(request))
    await asyncio.sleep(0)
    runtime.revoke_principal(request.principal_id)
    provider.release_first.set()

    first, waiting = await asyncio.gather(first_task, waiting_task)

    assert [first.value, waiting.value] == ["deny", "deny"]
    assert provider.calls == 1
    assert runtime.cached_allow_count == 0


@pytest.mark.asyncio
async def test_explicit_core_policy_invalidation_removes_cached_allows(tmp_path):
    class SequenceProvider:
        name = "sequence"

        def __init__(self):
            self.calls = 0

        def decide(self, request):
            self.calls += 1
            if self.calls == 1:
                return ApprovalDecision("always_allow_for_session")
            return ApprovalDecision("deny")

    provider = SequenceProvider()
    runtime = ApprovalRuntime(provider)
    request = _request(scope=_scope(tmp_path))

    first = await runtime.decide(request)
    removed = runtime.invalidate_core("assistant")
    second = await runtime.decide(request)

    assert first.value == "always_allow_for_session"
    assert removed == 1
    assert second.value == "deny"
    assert provider.calls == 2


def test_approval_event_and_operator_views_redact_secret_arguments(tmp_path):
    request = _request(
        scope=_scope(tmp_path),
        arguments_preview={
            "api_key": "super-secret-value",
            "command": "curl --token super-secret-value https://example.test",
            "nested": {
                "password": "another-secret-value",
                "path": "notes.txt",
            },
        },
        command="curl --api-key super-secret-value https://example.test",
        summary="fetch token=super-secret-value",
        target="https://example.test?token=super-secret-value",
        cache_key="write:super-secret-value",
    )

    view = request.redacted_view()
    encoded = json.dumps(view, ensure_ascii=False)

    assert request.arguments_preview == {
        "api_key": "<redacted>",
        "command": "curl --token <redacted> https://example.test",
        "nested": {
            "password": "<redacted>",
            "path": "notes.txt",
        },
    }
    assert "super-secret-value" not in encoded
    assert "another-secret-value" not in encoded
    assert request.command == "curl --api-key <redacted> https://example.test"
    assert request.summary == "fetch token=<redacted>"
    assert request.target == "https://example.test?token=<redacted>"
    assert "cache_key" not in view
    assert len(view["cache_key_fingerprint"]) == 16
    assert view["principal"] == {
        "principal_id": request.principal_id,
        "authority": "conversation",
        "channel": "test",
        "conversation_key": "test:dm:principal_A",
        "session_id": "session_A",
        "allowed_session_count": 1,
    }


def test_approval_secret_binding_metadata_exposes_names_not_values(tmp_path):
    request = _request(
        scope=_scope(tmp_path),
        arguments_preview={
            "secret_bindings": [
                {
                    "source": "env:DEMIURGE_TEST_SECRET",
                    "target": "BOUND_SECRET",
                    "capability": "secret.bind:DEMIURGE_TEST_SECRET",
                    "expires_at": "2026-07-13T12:00:00+00:00",
                    "value": "SYNTHETIC_SECRET_VALUE",
                }
            ]
        },
    )

    binding = request.redacted_view()["arguments_preview"]["secret_bindings"][0]
    assert binding == {
        "source": "env:DEMIURGE_TEST_SECRET",
        "target": "BOUND_SECRET",
        "capability": "secret.bind:DEMIURGE_TEST_SECRET",
        "expires_at": "2026-07-13T12:00:00+00:00",
        "value": "<redacted>",
    }
    assert "SYNTHETIC_SECRET_VALUE" not in json.dumps(request.redacted_view())


def test_approval_event_and_operator_text_fields_are_bounded(tmp_path):
    request = _request(
        scope=_scope(tmp_path),
        summary="s" * 5000,
        target="t" * 5000,
        command="c" * 5000,
    )
    view = request.redacted_view()

    assert len(view["summary"]) <= 540
    assert len(view["target"]) <= 1040
    assert len(view["command"]) <= 2040
    assert "[truncated" in view["summary"]
    assert "[truncated" in view["target"]
    assert "[truncated" in view["command"]


def test_approval_event_nested_command_and_url_fields_are_bounded(tmp_path):
    request = _request(
        scope=_scope(tmp_path),
        arguments_preview={
            "command": f"curl --token super-secret-value {'c' * 5000}",
            "url": f"https://example.test/?token=super-secret-value&{'u' * 5000}",
        },
    )
    preview = request.redacted_view()["arguments_preview"]

    assert "super-secret-value" not in json.dumps(preview)
    assert len(preview["command"]) <= 540
    assert len(preview["url"]) <= 540
    assert "[truncated" in preview["command"]
    assert "[truncated" in preview["url"]


def test_approval_redaction_failure_is_fail_closed(monkeypatch, tmp_path):
    secret = "SYNTHETIC_APPROVAL_FAIL_CLOSED_SECRET"

    def fail(*_args, **_kwargs):
        raise RuntimeError(f"redaction failed with {secret}")

    monkeypatch.setattr(SecretRedactor, "_redact_value", fail)

    request = _request(
        scope=_scope(tmp_path),
        arguments_preview={"token": secret},
        command=f"curl --token {secret}",
        summary=f"token={secret}",
        target=f"https://example.test?token={secret}",
    )

    assert request.arguments_preview == {"redaction": REDACTION_FAILED}
    assert request.command == REDACTION_FAILED
    assert request.summary == REDACTION_FAILED
    assert request.target == REDACTION_FAILED
    assert secret not in json.dumps(request.redacted_view())


def test_approval_known_secret_is_shared_across_all_preview_fields(tmp_path):
    secret = "SYNTHETIC_SHARED_APPROVAL_SECRET"

    request = _request(
        scope=_scope(tmp_path),
        arguments_preview={"token": secret},
        command=f"send {secret}",
        summary=f"send credential {secret}",
        target=f"custom target {secret}",
    )

    assert secret not in json.dumps(request.redacted_view())
    assert request.command == "send <redacted>"
    assert request.summary == "send credential <redacted>"
    assert request.target == "custom target <redacted>"


def test_tool_caller_cannot_override_host_issued_owner_fields(tmp_path):
    with pytest.raises(TypeError, match="session_id"):
        _request(
            scope=_scope(tmp_path, session_id="session_A"),
            session_id="session_B",
        )

    with pytest.raises(TypeError, match="turn_id"):
        _request(
            scope=_scope(tmp_path, session_id="session_A"),
            turn_id="forged_turn",
        )


def test_approval_scope_rejects_mismatched_execution_correlation(tmp_path):
    host_scope = _scope(tmp_path)
    context = SimpleNamespace(
        principal_scope=host_scope.principal_scope,
        session_id="session_A",
        core_id="assistant",
        core_revision="revision_1",
        capability_snapshot=host_scope.capability_snapshot,
        cancellation=SimpleNamespace(turn_id="turn_A"),
        admission_lease=SimpleNamespace(
            turn_id="turn_B",
            session_id="session_A",
        ),
    )

    with pytest.raises(ValueError, match="correlation"):
        ApprovalScope.from_execution_context(context)


@pytest.mark.asyncio
async def test_starting_new_session_invalidates_previous_session_allows(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    app.approval_runtime.provider = StaticApprovalProvider(
        "always_allow_for_session"
    )
    core = await app.runner.load_active_core()
    old_session_id = app.runner.session_id
    request = _request(
        scope=ApprovalScope.for_host_operation(
            principal_scope=app.runner.principal_scope,
            turn_id="turn_old",
            core_id=core.core_id,
            core_revision=core.revision,
            capability_snapshot=CapabilitySnapshot.capture(core),
        )
    )

    await app.approval_runtime.decide(request)
    new_session_id = app.runner.start_new_session()

    assert new_session_id != old_session_id
    assert app.approval_runtime.cached_allow_count == 0
    await app.close()


@pytest.mark.asyncio
async def test_app_close_revokes_all_cached_approval_authority(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    app.approval_runtime.provider = StaticApprovalProvider(
        "always_allow_for_session"
    )
    core = await app.runner.load_active_core()
    request = _request(
        scope=ApprovalScope.for_host_operation(
            principal_scope=app.runner.principal_scope,
            turn_id="turn_close",
            core_id=core.core_id,
            core_revision=core.revision,
            capability_snapshot=CapabilitySnapshot.capture(core),
        )
    )

    await app.approval_runtime.decide(request)
    assert app.approval_runtime.cached_allow_count == 1

    await app.close()

    assert app.approval_runtime.cached_allow_count == 0
    after_close = await app.approval_runtime.decide(request)
    assert after_close.value == "deny"
    assert after_close.reason == "approval runtime is closed"
