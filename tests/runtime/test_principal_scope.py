from dataclasses import FrozenInstanceError
from concurrent.futures import ThreadPoolExecutor
import gc
import json
import os
import sqlite3
import stat

import pytest

from demiurge import app as app_module
from demiurge.runtime import scope as scope_module
from demiurge.app import DemiurgeApp, create_app
from demiurge.runtime.interaction_factory import runtime_factory_for_app
from demiurge.runtime.interactions import InteractionInbound
from demiurge.runtime.scope import AuthorityKind, PrincipalScope, PrincipalScopeResolver
from demiurge.runtime.control import RuntimeControlPlane, TaskSource, TaskSpec
from demiurge.runtime.runner import SessionTurnStepRunner
from demiurge.runtime.session import SessionRuntime
from demiurge.runtime.store import RuntimeQuery, RuntimeStore
from tests.runtime.operator_authority_support import activate_test_operator_authority


def _host_store(path):
    store = RuntimeStore(path)
    activate_test_operator_authority(store)
    return store


def test_conversation_scope_is_host_derived_immutable_and_session_bounded(tmp_path):
    resolver = PrincipalScopeResolver(RuntimeStore(tmp_path / "runtime.sqlite3"))
    scope = resolver.issue_conversation(
        channel="slack",
        principal_key="U:1",
        conversation_key="slack:channel:T1:C1",
        session_id="session_1",
    )

    assert scope.principal_id == "principal:conversation:slack:U%3A1"
    assert scope.authority is AuthorityKind.CONVERSATION
    assert scope.channel == "slack"
    assert scope.conversation_key == "slack:channel:T1:C1"
    assert scope.session_id == "session_1"
    assert scope.allowed_session_ids == frozenset({"session_1"})
    assert scope.allows_session("session_1") is True
    assert scope.allows_session("session_2") is False

    with pytest.raises(FrozenInstanceError):
        scope.session_id = "session_2"

    with pytest.raises(TypeError, match="Host authority factory"):
        PrincipalScope(
            principal_id="forged",
            authority=AuthorityKind.OPERATOR,
            channel=None,
            conversation_key=None,
            session_id="session_2",
            allowed_session_ids=frozenset({"session_2"}),
        )


def test_local_operator_scope_has_explicit_bounded_cross_session_authority(tmp_path):
    store = _host_store(tmp_path / "runtime.sqlite3")
    sessions = SessionRuntime(control_plane=RuntimeControlPlane(store))
    for session_id in ("session_1", "session_2"):
        sessions.create_session(
            session_id=session_id,
            core_id="assistant",
            core_revision="rev",
        )
    scope = PrincipalScopeResolver(store).local_operator(
        active_session_id="session_1",
        reason="test operator session visibility",
    )

    assert scope.principal_id == "principal:operator:local"
    assert scope.authority is AuthorityKind.OPERATOR
    assert scope.channel is None
    assert scope.conversation_key is None
    assert scope.session_id == "session_1"
    assert scope.allowed_session_ids == frozenset({"session_1"})
    assert scope.allows_session("session_2") is False
    assert scope.allows_session("session_3") is False

    audit = store.query(
        RuntimeQuery(
            table="runtime_events",
            where={"type": "principal_scope.operator_issued"},
            limit=10,
        )
    ).rows
    assert len(audit) == 1
    assert audit[0]["payload"]["reason"] == "test operator session visibility"
    assert audit[0]["payload"]["active_session_id"] == "session_1"


def test_public_scope_factory_cannot_issue_or_restore_operator_authority():
    assert not hasattr(scope_module, "PrincipalScopeFactory")


def test_operator_authority_activator_requires_owning_app_lifecycle(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite3")

    with pytest.raises(PermissionError, match="owning DemiurgeApp"):
        scope_module._activate_operator_authority(store, object())

    forged_host = object.__new__(DemiurgeApp)
    forged_host.runtime_store = store
    forged_host._operator_authority = None
    forged_host._closed = False
    with pytest.raises(PermissionError, match="owning DemiurgeApp"):
        scope_module._activate_operator_authority(store, forged_host)


def test_collected_host_releases_operator_authority_lease(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    first_host = object.__new__(DemiurgeApp)
    first_host.runtime_store = store
    first_host._operator_authority = None
    first_host._closed = False
    app_module._ACTIVE_APP_LIFECYCLES[id(first_host)] = first_host
    first_host._operator_authority = scope_module._activate_operator_authority(
        store,
        first_host,
    )

    del first_host
    gc.collect()

    second_host = object.__new__(DemiurgeApp)
    second_host.runtime_store = store
    second_host._operator_authority = None
    second_host._closed = False
    app_module._ACTIVE_APP_LIFECYCLES[id(second_host)] = second_host

    second_host._operator_authority = scope_module._activate_operator_authority(
        store,
        second_host,
    )

    assert second_host._operator_authority is not None


def test_failed_app_bootstrap_invalidates_partial_host_lifecycle(tmp_path, monkeypatch):
    captured_apps = []

    def fail_bootstrap(_runner):
        captured_apps.extend(app_module._ACTIVE_APP_LIFECYCLES.values())
        raise RuntimeError("synthetic bootstrap failure")

    monkeypatch.setattr(
        SessionTurnStepRunner,
        "_ensure_current_session",
        fail_bootstrap,
    )

    with pytest.raises(RuntimeError, match="synthetic bootstrap failure"):
        create_app(home=tmp_path / "home", provider_name="fake")

    partial_app = captured_apps[-1]
    assert partial_app._closed is True
    assert partial_app._operator_authority is None
    assert app_module._ACTIVE_APP_LIFECYCLES.get(id(partial_app)) is None
    with pytest.raises(PermissionError, match="owning DemiurgeApp"):
        scope_module._activate_operator_authority(
            partial_app.runtime_store,
            partial_app,
        )


def test_runtime_store_rejects_scope_issued_by_another_store(tmp_path):
    first = _host_store(tmp_path / "first.sqlite3")
    second = RuntimeStore(tmp_path / "second.sqlite3")
    first_sessions = SessionRuntime(control_plane=RuntimeControlPlane(first))
    second_sessions = SessionRuntime(control_plane=RuntimeControlPlane(second))
    first_sessions.create_session(
        session_id="session_1",
        core_id="assistant",
        core_revision="rev",
    )
    second_sessions.create_session(
        session_id="session_1",
        core_id="assistant",
        core_revision="rev",
    )
    scope = PrincipalScopeResolver(first).local_operator(
        active_session_id="session_1",
        reason="cross-store rejection probe",
    )

    with pytest.raises(PermissionError, match="issued by this RuntimeStore"):
        second.query_owned(
            scope,
            RuntimeQuery(table="sessions", where={"session_id": "session_1"}, limit=1),
        )


def test_reopened_store_cannot_self_issue_operator_authority(tmp_path):
    path = tmp_path / "runtime.sqlite3"
    host_store = _host_store(path)
    resolver = PrincipalScopeResolver(host_store)
    scope = resolver.issue_conversation(
        channel="slack",
        principal_key="user_a",
        conversation_key="slack:channel:T1:A",
        session_id="session_a",
    )
    SessionRuntime(control_plane=RuntimeControlPlane(host_store)).create_session(
        session_id="session_a",
        core_id="assistant",
        core_revision="rev",
        principal_scope=scope,
    )
    reopened = RuntimeStore(path)

    with pytest.raises(PermissionError, match="active Host"):
        PrincipalScopeResolver(reopened).local_operator(
            active_session_id="session_a",
            reason="forged reopened store",
        )


def test_reopened_store_cannot_enable_operator_authority_by_setting_runtime_attribute(tmp_path):
    path = tmp_path / "runtime.sqlite3"
    host_store = _host_store(path)
    resolver = PrincipalScopeResolver(host_store)
    owner_scope = resolver.issue_conversation(
        channel="slack",
        principal_key="user_a",
        conversation_key="slack:channel:T1:A",
        session_id="session_a",
    )
    SessionRuntime(control_plane=RuntimeControlPlane(host_store)).create_session(
        session_id="session_a",
        core_id="assistant",
        core_revision="rev",
        principal_scope=owner_scope,
    )
    reopened = RuntimeStore(path)
    reopened._operator_authority_issuer = object()

    with pytest.raises(PermissionError, match="active Host"):
        PrincipalScopeResolver(reopened).local_operator(
            active_session_id="session_a",
            reason="forged mutable runtime flag",
        )


@pytest.mark.asyncio
async def test_app_close_revokes_previously_issued_operator_scope(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    app.runner._ensure_current_session()
    scope = PrincipalScopeResolver(app.runtime_store).local_operator(
        active_session_id=app.runner.session_id,
        reason="verify close revokes operator authority",
    )
    query = RuntimeQuery(
        table="sessions",
        where={"session_id": app.runner.session_id},
        limit=1,
    )

    assert len(app.runtime_store.query_owned(scope, query).rows) == 1

    await app.close()

    with pytest.raises(PermissionError, match="active Host"):
        app.runtime_store.query_owned(scope, query)
    with pytest.raises(PermissionError, match="owning DemiurgeApp"):
        scope_module._activate_operator_authority(app.runtime_store, app)


@pytest.mark.asyncio
async def test_app_close_revokes_operator_scope_when_tool_shutdown_fails(tmp_path, monkeypatch):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    scope = PrincipalScopeResolver(app.runtime_store).local_operator(
        active_session_id=app.runner.session_id,
        reason="verify failed close revokes operator authority",
    )
    query = RuntimeQuery(
        table="sessions",
        where={"session_id": app.runner.session_id},
        limit=1,
    )

    async def fail_tool_shutdown():
        raise RuntimeError("synthetic tool shutdown failure")

    monkeypatch.setattr(app.tool_runtime, "close", fail_tool_shutdown)

    with pytest.raises(RuntimeError, match="synthetic tool shutdown failure"):
        await app.close()

    with pytest.raises(PermissionError, match="active Host"):
        app.runtime_store.query_owned(scope, query)


def test_provisional_conversation_scope_cannot_query_before_owner_validation(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    sessions = SessionRuntime(control_plane=RuntimeControlPlane(store))
    resolver = PrincipalScopeResolver(store)
    owner_scope = resolver.issue_conversation(
        channel="slack",
        principal_key="user_b",
        conversation_key="slack:channel:T1:B",
        session_id="session_b",
    )
    sessions.create_session(
        session_id="session_b",
        core_id="assistant",
        core_revision="rev",
        channel="slack",
        conversation_key="slack:channel:T1:B",
        principal_scope=owner_scope,
    )
    forged_provisional = resolver.issue_conversation(
        channel="slack",
        principal_key="user_a",
        conversation_key="slack:channel:T1:A",
        session_id="session_b",
    )

    with pytest.raises(PermissionError, match="durable owner"):
        store.query_owned(
            forged_provisional,
            RuntimeQuery(table="sessions", where={"session_id": "session_b"}, limit=1),
        )


def test_operator_owned_query_is_relational_above_sqlite_bind_limit(tmp_path):
    store = _host_store(tmp_path / "runtime.sqlite3")
    session_ids = [f"session_{index:05d}" for index in range(33_000)]

    def insert_sessions_and_owners(connection):
        connection.executemany(
            """
            INSERT INTO sessions (
                session_id, core_id, status, channel, target_json, created_at, updated_at
            ) VALUES (?, 'assistant', 'active', NULL, '{}', ?, ?)
            """,
            [
                (
                    session_id,
                    "2026-07-11T00:00:00Z",
                    "2026-07-11T00:00:00Z",
                )
                for session_id in session_ids
            ],
        )
        connection.executemany(
            """
            INSERT INTO session_owners (
                session_id, owner_kind, principal_id, channel, conversation_key,
                origin_session_id, origin_turn_id, created_at, updated_at
            ) VALUES (?, 'legacy_local', ?, NULL, NULL, NULL, NULL, ?, ?)
            """,
            [
                (
                    session_id,
                    f"principal:legacy_local:{session_id}",
                    "2026-07-11T00:00:00Z",
                    "2026-07-11T00:00:00Z",
                )
                for session_id in session_ids
            ],
        )

    store.transaction(insert_sessions_and_owners)
    scope = PrincipalScopeResolver(store).local_operator(
        active_session_id=session_ids[-1],
        reason="operator relational owner query regression",
    )
    guessed = store.query_owned(
        scope,
        RuntimeQuery(
            table="sessions",
            where={"session_id": session_ids[0]},
            limit=1,
        ),
    )

    assert scope.allowed_session_ids == frozenset({session_ids[-1]})
    assert scope.allows_session(session_ids[0]) is False
    assert scope.allows_session(session_ids[-1]) is True
    assert [row["session_id"] for row in guessed.rows] == [session_ids[0]]


def test_legacy_local_origin_scope_fails_closed_without_operator_repair(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    SessionRuntime(control_plane=RuntimeControlPlane(store)).create_session(
        session_id="session_legacy",
        core_id="assistant",
        core_revision="rev",
    )

    with pytest.raises(PermissionError, match="explicit operator repair"):
        PrincipalScopeResolver(store).origin_scope(session_id="session_legacy")


def test_app_runner_persists_explicit_local_operator_owner(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")

    owner = app.runtime_store.query(
        RuntimeQuery(
            table="session_owners",
            where={"session_id": app.runner.session_id},
            limit=1,
        )
    ).rows[0]
    scope = PrincipalScopeResolver(app.runtime_store).local_operator(
        active_session_id=app.runner.session_id,
        reason="inspect app runner operator owner",
    )

    assert owner["owner_kind"] == "operator"
    assert owner["principal_id"] == "principal:operator:local"
    assert scope.authority is AuthorityKind.OPERATOR
    assert scope.allows_session(app.runner.session_id) is True


@pytest.mark.asyncio
async def test_tui_turn_keeps_explicit_operator_authority(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    session_id = app.runner.session_id

    result = await app.runner.run_turn(
        "hello",
        interaction=InteractionInbound(
            channel="tui",
            text="hello",
            source="local",
            principal_key="local-operator",
            conversation_key=f"tui:{session_id}",
        ),
    )
    owner = app.runtime_store.query(
        RuntimeQuery(
            table="session_owners",
            where={"session_id": result.session_id},
            limit=1,
        )
    ).rows[0]

    assert result.session_id == session_id
    assert owner["owner_kind"] == "operator"
    assert app.runner.principal_scope.authority is AuthorityKind.OPERATOR
    await app.close()


def test_scheduled_run_scope_is_system_authority_limited_to_its_session(tmp_path):
    scope = PrincipalScopeResolver(RuntimeStore(tmp_path / "runtime.sqlite3")).scheduled_run(
        core_id="assistant",
        schedule_id="daily:summary",
        run_id="run_1",
        session_id="session_schedule_1",
    )

    assert scope.principal_id == "principal:scheduler:assistant:daily%3Asummary:run_1"
    assert scope.authority is AuthorityKind.SYSTEM
    assert scope.channel is None
    assert scope.conversation_key is None
    assert scope.allowed_session_ids == frozenset({"session_schedule_1"})
    assert scope.allows_session("session_parent") is False


def test_delegated_agent_scope_owns_child_session_not_parent_session(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    resolver = PrincipalScopeResolver(store)
    parent = resolver.issue_conversation(
        channel="telegram",
        principal_key="123",
        conversation_key="telegram:dm:123",
        session_id="session_parent",
    )
    SessionRuntime(control_plane=RuntimeControlPlane(store)).create_session(
        session_id="session_parent",
        core_id="assistant",
        core_revision="rev",
        principal_scope=parent,
    )
    parent = resolver.conversation(
        channel="telegram",
        principal_key="123",
        conversation_key="telegram:dm:123",
        session_id="session_parent",
    )

    child = resolver.delegated_agent(
        parent=parent,
        task_id="task_1",
        parent_turn_id="turn_parent",
        child_session_id="session_child",
    )

    assert child.authority is AuthorityKind.DELEGATED_AGENT
    assert child.session_id == "session_child"
    assert child.allowed_session_ids == frozenset({"session_child"})
    assert child.allows_session("session_parent") is False
    assert child.origin_session_id == "session_parent"
    assert child.origin_turn_id == "turn_parent"
    assert child.principal_id == (
        "principal:delegated_agent:principal%3Aconversation%3Atelegram%3A123:task_1"
    )


def test_background_completion_reuses_persisted_origin_scope_without_elevation(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    sessions = SessionRuntime(control_plane=RuntimeControlPlane(store))
    resolver = PrincipalScopeResolver(store)
    origin = resolver.issue_conversation(
        channel="email",
        principal_key="alice@example.com",
        conversation_key="email:sender:alice%40example.com",
        session_id="session_origin",
    )

    sessions.create_session(
        session_id="session_origin",
        core_id="assistant",
        core_revision="rev",
        channel="email",
        conversation_key="email:sender:alice%40example.com",
        principal_scope=origin,
    )
    origin = resolver.conversation(
        channel="email",
        principal_key="alice@example.com",
        conversation_key="email:sender:alice%40example.com",
        session_id="session_origin",
    )
    completion = resolver.background_completion(
        origin_record=resolver.capture_origin_record(
            scope=origin,
            owner_session_id="session_origin",
        ),
        owner_session_id="session_origin",
    )

    assert completion == origin
    assert completion.authority is AuthorityKind.CONVERSATION
    assert completion.allowed_session_ids == frozenset({"session_origin"})


@pytest.mark.asyncio
async def test_external_ingress_persists_scope_from_host_principal_key_not_route_metadata(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    conversation_key = "slack:channel:T1:C1"
    runtime = runtime_factory_for_app(app)(conversation_key)

    outbound = await runtime.handle(
        InteractionInbound(
            channel="slack",
            text="hello",
            source="route:C1",
            principal_key="team:T1:channel:C1",
            conversation_key=conversation_key,
            metadata={
                "principal_id": "forged",
                "allowed_session_ids": ["session_other"],
            },
        )
    )

    scope = PrincipalScopeResolver(app.runtime_store).conversation(
        channel="slack",
        principal_key="team:T1:channel:C1",
        conversation_key=conversation_key,
        session_id=outbound.session_id,
    )
    owner = app.runtime_store.query(
        RuntimeQuery(
            table="session_owners",
            where={"session_id": outbound.session_id},
            limit=1,
        )
    ).rows[0]

    assert scope.session_id == outbound.session_id
    assert scope.allowed_session_ids == frozenset({outbound.session_id})
    assert owner["principal_id"] == "principal:conversation:slack:team%3AT1%3Achannel%3AC1"
    assert owner["principal_id"] != "forged"
    await app.close()


@pytest.mark.asyncio
async def test_external_manual_resume_requires_same_durable_principal_and_conversation(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    runtime_a = runtime_factory_for_app(app)("slack:channel:T1:A")
    runtime_b = runtime_factory_for_app(app)("slack:channel:T1:B")
    outbound_a = await runtime_a.handle(
        InteractionInbound(
            channel="slack",
            text="A",
            source="route:A",
            principal_key="team:T1:channel:A",
            conversation_key="slack:channel:T1:A",
        )
    )
    outbound_b = await runtime_b.handle(
        InteractionInbound(
            channel="slack",
            text="B",
            source="route:B",
            principal_key="team:T1:channel:B",
            conversation_key="slack:channel:T1:B",
        )
    )

    runtime_a.runner.resume_session(
        outbound_a.session_id,
        channel="slack",
        principal_key="team:T1:channel:A",
        conversation_key="slack:channel:T1:A",
    )
    with pytest.raises(FileNotFoundError, match="session not found"):
        runtime_a.runner.resume_session(
            outbound_b.session_id,
            channel="slack",
            principal_key="team:T1:channel:A",
            conversation_key="slack:channel:T1:A",
        )

    await app.close()


def test_scoped_session_query_applies_owner_predicate_inside_store(tmp_path):
    store = _host_store(tmp_path / "runtime.sqlite3")
    sessions = SessionRuntime(control_plane=RuntimeControlPlane(store))
    resolver = PrincipalScopeResolver(store)
    for suffix in ("a", "b"):
        session_id = f"session_{suffix}"
        conversation_key = f"slack:channel:T1:{suffix.upper()}"
        principal_key = f"user_{suffix}"
        sessions.create_session(
            session_id=session_id,
            core_id="assistant",
            core_revision="rev",
            channel="slack",
            conversation_key=conversation_key,
            metadata={"source": principal_key},
            principal_scope=resolver.issue_conversation(
                channel="slack",
                principal_key=principal_key,
                conversation_key=conversation_key,
                session_id=session_id,
            ),
        )
    scope_a = resolver.conversation(
        channel="slack",
        principal_key="user_a",
        conversation_key="slack:channel:T1:A",
        session_id="session_a",
    )
    operator = resolver.local_operator(
        active_session_id="session_a",
        reason="owned session query test",
    )

    scoped = store.query_owned(scope_a, RuntimeQuery(table="sessions", order_by="created_at", limit=10))
    guessed = store.query_owned(
        scope_a,
        RuntimeQuery(table="sessions", where={"session_id": "session_b"}, limit=1),
    )
    operator_rows = store.query_owned(
        operator,
        RuntimeQuery(table="sessions", order_by="created_at", limit=10),
    )

    assert [row["session_id"] for row in scoped.rows] == ["session_a"]
    assert guessed.rows == ()
    assert {row["session_id"] for row in operator_rows.rows} == {"session_a", "session_b"}


def test_scoped_task_query_filters_guessed_cross_session_task_inside_store(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    control = RuntimeControlPlane(store)
    sessions = SessionRuntime(control_plane=control)
    resolver = PrincipalScopeResolver(store)
    for suffix in ("a", "b"):
        session_id = f"session_{suffix}"
        conversation_key = f"slack:channel:T1:{suffix.upper()}"
        sessions.create_session(
            session_id=session_id,
            core_id="assistant",
            core_revision="rev",
            channel="slack",
            conversation_key=conversation_key,
            principal_scope=resolver.issue_conversation(
                channel="slack",
                principal_key=f"user_{suffix}",
                conversation_key=conversation_key,
                session_id=session_id,
            ),
        )
    task_a = control.submit_task(
        TaskSpec(kind="terminal.exec"),
        source=TaskSource(actor="test", session_id="session_a", turn_id="turn_a"),
    )
    task_b = control.submit_task(
        TaskSpec(kind="terminal.exec"),
        source=TaskSource(actor="test", session_id="session_b", turn_id="turn_b"),
    )
    scope_a = resolver.conversation(
        channel="slack",
        principal_key="user_a",
        conversation_key="slack:channel:T1:A",
        session_id="session_a",
    )

    visible = store.query_owned(scope_a, RuntimeQuery(table="tasks", order_by="created_at", limit=10))
    guessed = store.query_owned(
        scope_a,
        RuntimeQuery(table="tasks", where={"task_id": task_b.task_id}, limit=1),
    )

    assert [row["task_id"] for row in visible.rows] == [task_a.task_id]
    assert guessed.rows == ()


def test_scoped_message_query_hides_cross_session_history_inside_store(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    sessions = SessionRuntime(control_plane=RuntimeControlPlane(store))
    resolver = PrincipalScopeResolver(store)
    for suffix in ("a", "b"):
        session_id = f"session_{suffix}"
        conversation_key = f"matrix:room:%21{suffix}%3Aexample.com"
        sessions.create_session(
            session_id=session_id,
            core_id="assistant",
            core_revision="rev",
            channel="matrix",
            conversation_key=conversation_key,
            principal_scope=resolver.issue_conversation(
                channel="matrix",
                principal_key=f"@{suffix}:example.com",
                conversation_key=conversation_key,
                session_id=session_id,
            ),
        )
        sessions.append_message(session_id, role="user", content=f"secret for {session_id}")
    scope_a = resolver.conversation(
        channel="matrix",
        principal_key="@a:example.com",
        conversation_key="matrix:room:%21a%3Aexample.com",
        session_id="session_a",
    )

    visible = store.query_owned(scope_a, RuntimeQuery(table="messages", order_by="runtime_seq", limit=10))
    guessed = store.query_owned(
        scope_a,
        RuntimeQuery(table="messages", where={"session_id": "session_b"}, limit=10),
    )

    assert [row["session_id"] for row in visible.rows] == ["session_a"]
    assert guessed.rows == ()


def test_scope_persistence_round_trip_and_redacted_view_preserve_authority(tmp_path):
    store = _host_store(tmp_path / "runtime.sqlite3")
    sessions = SessionRuntime(control_plane=RuntimeControlPlane(store))
    resolver = PrincipalScopeResolver(store)
    scope_a = resolver.local_operator(
        active_session_id="session_a",
        reason="bootstrap operator session a",
        allow_unowned_active=True,
    )
    sessions.create_session(
        session_id="session_a",
        core_id="assistant",
        core_revision="rev",
        principal_scope=scope_a,
    )
    scope_b = resolver.local_operator(
        active_session_id="session_b",
        reason="bootstrap operator session b",
        allow_unowned_active=True,
    )
    sessions.create_session(
        session_id="session_b",
        core_id="assistant",
        core_revision="rev",
        principal_scope=scope_b,
    )
    scope = resolver.local_operator(
        active_session_id="session_a",
        reason="round-trip operator scope",
    )

    restored = resolver.origin_scope(session_id="session_a")
    redacted = scope.redacted_view()

    assert restored == scope
    assert redacted == {
        "principal_id": "principal:operator:local",
        "authority": "operator",
        "channel": None,
        "conversation_key": None,
        "session_id": "session_a",
        "allowed_session_count": 1,
    }
    assert "allowed_session_ids" not in redacted


def test_runtime_store_migrates_ambiguous_v4_session_to_legacy_local_idempotently(tmp_path):
    path = tmp_path / "runtime.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE sessions (
                session_id TEXT PRIMARY KEY,
                core_id TEXT NOT NULL,
                status TEXT NOT NULL,
                channel TEXT,
                target_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE session_bindings (
                core_id TEXT NOT NULL,
                channel TEXT NOT NULL,
                conversation_key TEXT NOT NULL,
                session_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (core_id, channel, conversation_key)
            );
            PRAGMA user_version = 4;
            """
        )
        connection.execute(
            """
            INSERT INTO sessions (
                session_id, core_id, status, channel, target_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "session_legacy",
                "assistant",
                "active",
                None,
                json.dumps({"metadata": {}}),
                "2026-07-11T00:00:00Z",
                "2026-07-11T00:00:00Z",
            ),
        )
        connection.execute(
            """
            INSERT INTO sessions (
                session_id, core_id, status, channel, target_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "session_bound",
                "assistant",
                "active",
                "slack",
                json.dumps(
                    {
                        "conversation_key": "slack:channel:T1:C1",
                        "metadata": {"source": "U1"},
                    }
                ),
                "2026-07-11T00:00:01Z",
                "2026-07-11T00:00:01Z",
            ),
        )
        connection.execute(
            """
            INSERT INTO session_bindings (
                core_id, channel, conversation_key, session_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "assistant",
                "slack",
                "slack:channel:T1:C1",
                "session_bound",
                "2026-07-11T00:00:01Z",
                "2026-07-11T00:00:01Z",
            ),
        )

    first = RuntimeStore(path)
    first_rows = first.query(
        RuntimeQuery(table="session_owners", where={"session_id": "session_legacy"}, limit=1)
    ).rows
    second = RuntimeStore(path)
    second_rows = second.query(
        RuntimeQuery(table="session_owners", where={"session_id": "session_legacy"}, limit=1)
    ).rows

    assert first_rows == second_rows
    assert first_rows == (
        {
            "session_id": "session_legacy",
            "owner_kind": "legacy_local",
            "principal_id": "principal:legacy_local:session_legacy",
            "channel": None,
            "conversation_key": None,
            "origin_session_id": None,
            "origin_turn_id": None,
            "created_at": "2026-07-11T00:00:00Z",
            "updated_at": "2026-07-11T00:00:00Z",
        },
    )
    assert path.with_name("runtime.sqlite3.v4.bak").exists()
    if os.name != "nt":
        assert stat.S_IMODE(path.with_name("runtime.sqlite3.v4.bak").stat().st_mode) == 0o600
    bound = first.query(
        RuntimeQuery(table="session_owners", where={"session_id": "session_bound"}, limit=1)
    ).rows[0]
    assert bound["owner_kind"] == "conversation"
    assert bound["principal_id"] == "principal:conversation:slack:slack%3Achannel%3AT1%3AC1"
    assert bound["conversation_key"] == "slack:channel:T1:C1"
    restored_bound_scope = PrincipalScopeResolver(first).conversation(
        channel="slack",
        principal_key="slack:channel:T1:C1",
        conversation_key="slack:channel:T1:C1",
        session_id="session_bound",
    )
    assert restored_bound_scope.session_id == "session_bound"
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 5


def test_session_creation_persists_scope_or_fails_closed_to_legacy_local(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    sessions = SessionRuntime(control_plane=RuntimeControlPlane(store))
    resolver = PrincipalScopeResolver(store)
    scope = resolver.issue_conversation(
        channel="telegram",
        principal_key="123",
        conversation_key="telegram:dm:123",
        session_id="session_owned",
    )

    sessions.create_session(
        session_id="session_owned",
        core_id="assistant",
        core_revision="rev",
        channel="telegram",
        conversation_key="telegram:dm:123",
        metadata={"source": "123"},
        principal_scope=scope,
    )
    scope = resolver.conversation(
        channel="telegram",
        principal_key="123",
        conversation_key="telegram:dm:123",
        session_id="session_owned",
    )
    sessions.create_session(
        session_id="session_ambiguous",
        core_id="assistant",
        core_revision="rev",
    )
    child = resolver.delegated_agent(
        parent=scope,
        task_id="task_child",
        parent_turn_id="turn_parent",
        child_session_id="session_child",
    )
    sessions.create_session(
        session_id="session_child",
        core_id="assistant",
        core_revision="rev",
        principal_scope=child,
    )

    owners = store.query(RuntimeQuery(table="session_owners", order_by="created_at", limit=10)).rows
    by_session = {row["session_id"]: row for row in owners}
    assert by_session["session_owned"]["owner_kind"] == "conversation"
    assert by_session["session_owned"]["principal_id"] == "principal:conversation:telegram:123"
    assert by_session["session_ambiguous"]["owner_kind"] == "legacy_local"
    assert by_session["session_child"]["owner_kind"] == "delegated_agent"
    assert by_session["session_child"]["origin_session_id"] == "session_owned"
    assert by_session["session_child"]["origin_turn_id"] == "turn_parent"
    persisted_child_scope = resolver.origin_scope(session_id="session_child")
    completion_scope = resolver.background_completion(
        origin_record=resolver.capture_origin_record(
            scope=persisted_child_scope,
            owner_session_id="session_child",
        ),
        owner_session_id="session_child",
    )
    assert completion_scope.authority is AuthorityKind.DELEGATED_AGENT
    assert completion_scope.allowed_session_ids == frozenset({"session_child"})


def test_session_owner_is_immutable_after_creation(tmp_path):
    store = _host_store(tmp_path / "runtime.sqlite3")
    sessions = SessionRuntime(control_plane=RuntimeControlPlane(store))
    resolver = PrincipalScopeResolver(store)
    scope_a = resolver.issue_conversation(
        channel="slack",
        principal_key="team:T1:channel:A",
        conversation_key="slack:channel:T1:A",
        session_id="session_a",
    )
    sessions.create_session(
        session_id="session_a",
        core_id="assistant",
        core_revision="rev",
        channel="slack",
        conversation_key="slack:channel:T1:A",
        principal_scope=scope_a,
    )
    forged = resolver.local_operator(
        active_session_id="session_a",
        reason="attempt immutable owner replacement",
    )

    sessions.ensure_session(
        "session_a",
        core_id="assistant",
        core_revision="rev",
        principal_scope=forged,
    )

    owner = store.query(
        RuntimeQuery(
            table="session_owners",
            where={"session_id": "session_a"},
            limit=1,
        )
    ).rows[0]
    assert owner["owner_kind"] == "conversation"
    assert owner["principal_id"] == scope_a.principal_id


def test_failed_owner_migration_preserves_v4_database_and_backup(tmp_path):
    path = tmp_path / "runtime.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE sessions (
                session_id TEXT PRIMARY KEY,
                core_id TEXT NOT NULL,
                status TEXT NOT NULL,
                channel TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE session_bindings (
                core_id TEXT NOT NULL,
                channel TEXT NOT NULL,
                conversation_key TEXT NOT NULL,
                session_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (core_id, channel, conversation_key)
            );
            PRAGMA user_version = 4;
            """
        )

    backup_path = path.with_name("runtime.sqlite3.v4.bak")
    with pytest.raises(RuntimeError) as failure:
        RuntimeStore(path)

    message = str(failure.value)
    assert str(path.resolve()) in message
    assert str(backup_path.resolve()) in message
    assert "original database remains unchanged" in message
    assert "restore" in message.lower()

    assert backup_path.exists()
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 4
        owner_table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'session_owners'"
        ).fetchone()
    assert owner_table is None


def test_runtime_store_owner_migration_is_safe_under_concurrent_open(tmp_path):
    path = tmp_path / "runtime.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE sessions (
                session_id TEXT PRIMARY KEY,
                core_id TEXT NOT NULL,
                status TEXT NOT NULL,
                channel TEXT,
                target_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE session_bindings (
                core_id TEXT NOT NULL,
                channel TEXT NOT NULL,
                conversation_key TEXT NOT NULL,
                session_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (core_id, channel, conversation_key)
            );
            PRAGMA user_version = 4;
            """
        )
        connection.execute(
            """
            INSERT INTO sessions (
                session_id, core_id, status, channel, target_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "session_legacy",
                "assistant",
                "active",
                None,
                json.dumps({"metadata": {}}),
                "2026-07-11T00:00:00Z",
                "2026-07-11T00:00:00Z",
            ),
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        stores = list(pool.map(lambda _: RuntimeStore(path), range(4)))

    assert all(
        store.query(
            RuntimeQuery(
                table="session_owners",
                where={"session_id": "session_legacy"},
                limit=1,
            )
        ).rows[0]["owner_kind"]
        == "legacy_local"
        for store in stores
    )
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 5
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_owner_migration_rejects_invalid_existing_backup_without_touching_v4_database(tmp_path):
    path = tmp_path / "runtime.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE sessions (
                session_id TEXT PRIMARY KEY,
                core_id TEXT NOT NULL,
                status TEXT NOT NULL,
                channel TEXT,
                target_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE session_bindings (
                core_id TEXT NOT NULL,
                channel TEXT NOT NULL,
                conversation_key TEXT NOT NULL,
                session_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (core_id, channel, conversation_key)
            );
            PRAGMA user_version = 4;
            """
        )
    path.with_name("runtime.sqlite3.v4.bak").write_bytes(b"not a sqlite database")

    with pytest.raises(RuntimeError, match="backup"):
        RuntimeStore(path)

    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 4
        owner_table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'session_owners'"
        ).fetchone()
    assert owner_table is None


def test_owner_migration_rejects_valid_backup_from_different_v4_database(tmp_path):
    path = tmp_path / "runtime.sqlite3"
    backup_path = path.with_name("runtime.sqlite3.v4.bak")

    def create_v4(database_path, session_id):
        with sqlite3.connect(database_path) as connection:
            connection.executescript(
                """
                CREATE TABLE sessions (
                    session_id TEXT PRIMARY KEY,
                    core_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    channel TEXT,
                    target_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE session_bindings (
                    core_id TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    conversation_key TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (core_id, channel, conversation_key)
                );
                PRAGMA user_version = 4;
                """
            )
            connection.execute(
                """
                INSERT INTO sessions (
                    session_id, core_id, status, channel, target_json, created_at, updated_at
                ) VALUES (?, 'assistant', 'active', NULL, '{}', ?, ?)
                """,
                (session_id, "2026-07-11T00:00:00Z", "2026-07-11T00:00:00Z"),
            )

    create_v4(path, "session_current")
    create_v4(backup_path, "session_old")

    with pytest.raises(RuntimeError, match="does not match the current database"):
        RuntimeStore(path)

    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 4
        assert connection.execute("SELECT session_id FROM sessions").fetchone()[0] == "session_current"
    with sqlite3.connect(backup_path) as connection:
        assert connection.execute("SELECT session_id FROM sessions").fetchone()[0] == "session_old"


def test_scope_resolver_requires_durable_owner_match_and_operator_is_explicit(tmp_path):
    store = _host_store(tmp_path / "runtime.sqlite3")
    sessions = SessionRuntime(control_plane=RuntimeControlPlane(store))
    resolver = PrincipalScopeResolver(store)
    for suffix in ("a", "b"):
        session_id = f"session_{suffix}"
        source = f"user_{suffix}"
        conversation_key = f"slack:channel:T1:{suffix.upper()}"
        scope = resolver.issue_conversation(
            channel="slack",
            principal_key=source,
            conversation_key=conversation_key,
            session_id=session_id,
        )
        sessions.create_session(
            session_id=session_id,
            core_id="assistant",
            core_revision="rev",
            channel="slack",
            conversation_key=conversation_key,
            metadata={"source": source},
            principal_scope=scope,
        )
    scope_a = resolver.conversation(
        channel="slack",
        principal_key="user_a",
        conversation_key="slack:channel:T1:A",
        session_id="session_a",
    )
    operator = resolver.local_operator(
        active_session_id="session_a",
        reason="explicit operator resolver test",
    )

    assert scope_a.allowed_session_ids == frozenset({"session_a"})
    assert operator.allowed_session_ids == frozenset({"session_a"})
    with pytest.raises(FileNotFoundError, match="session not found"):
        resolver.conversation(
            channel="slack",
            principal_key="user_a",
            conversation_key="slack:channel:T1:A",
            session_id="session_b",
        )


def test_session_runtime_owned_interface_hides_guessed_session_and_lists_by_scope(tmp_path):
    store = _host_store(tmp_path / "runtime.sqlite3")
    sessions = SessionRuntime(control_plane=RuntimeControlPlane(store))
    resolver = PrincipalScopeResolver(store)
    scopes = {}
    for suffix in ("a", "b"):
        session_id = f"session_{suffix}"
        scope = resolver.issue_conversation(
            channel="mattermost",
            principal_key=f"user_{suffix}",
            conversation_key=f"mattermost:channel:{suffix.upper()}",
            session_id=session_id,
        )
        sessions.create_session(
            session_id=session_id,
            core_id="assistant",
            core_revision="rev",
            principal_scope=scope,
        )
        scopes[suffix] = resolver.conversation(
            channel="mattermost",
            principal_key=f"user_{suffix}",
            conversation_key=f"mattermost:channel:{suffix.upper()}",
            session_id=session_id,
        )
    operator = resolver.local_operator(
        active_session_id="session_a",
        reason="owned session runtime listing",
    )

    assert sessions.get_owned_session(scopes["a"], "session_a").session_id == "session_a"
    assert [record.session_id for record in sessions.list_owned_sessions(scopes["a"], limit=10)] == ["session_a"]
    assert {record.session_id for record in sessions.list_owned_sessions(operator, limit=10)} == {
        "session_a",
        "session_b",
    }
    with pytest.raises(FileNotFoundError, match="session not found"):
        sessions.get_owned_session(scopes["a"], "session_b")
