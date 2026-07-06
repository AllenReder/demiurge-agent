import asyncio
from datetime import datetime, timezone

import pytest

from demiurge.app import create_app
from demiurge.providers import LLMResponse, ToolCall
from demiurge.runtime.control import RuntimeControlPlane
from demiurge.runtime.durable_work import DurableWorkRuntime
from demiurge.runtime.session import SessionQuery, SessionRuntime
from demiurge.runtime.store import RuntimeQuery, RuntimeStore
from demiurge.sdk import AgentInput, TurnContext
from demiurge.security.capabilities import CapabilityFacade


UTC = timezone.utc


class StaticProvider:
    async def complete(self, request):
        return LLMResponse(content="assistant reply")


class RaisingProvider:
    async def complete(self, request):
        raise RuntimeError("provider exploded")


class ToolThenAnswerProvider:
    def __init__(self):
        self.calls = 0

    async def complete(self, request):
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(tool_calls=[ToolCall(id="tools_1", name="tools_list", arguments={})])
        return LLMResponse(content="tool complete")


class BlockingProvider:
    def __init__(self):
        self.started = asyncio.Event()

    async def complete(self, request):
        self.started.set()
        await asyncio.Event().wait()


def test_session_runtime_projects_session_turn_and_messages(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    runtime = SessionRuntime(control_plane=RuntimeControlPlane(store))

    record, created = runtime.ensure_session(
        "session_1",
        core_id="assistant",
        core_revision="0001",
        channel="tui",
        conversation_key="local",
    )
    runtime.start_turn(session_id=record.session_id, turn_id="turn_1", input_ref="inbound:1")
    message = runtime.append_message(record.session_id, role="user", content="hello", turn_id="turn_1")
    runtime.complete_turn(session_id=record.session_id, turn_id="turn_1", result_ref="result:1")

    snapshot = runtime.read(SessionQuery(record.session_id, include_messages=True, include_turns=True))

    assert created is True
    assert snapshot.session["session_id"] == "session_1"
    assert snapshot.messages[0]["message_id"] == message.id
    assert snapshot.messages[0]["content"]["text"] == "hello"
    assert snapshot.turns[0]["turn_id"] == "turn_1"
    assert snapshot.turns[0]["status"] == "completed"
    assert snapshot.turns[0]["result_ref"] == "result:1"


def test_session_runtime_appends_delivery_message_and_outbox_atomically(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    runtime = SessionRuntime(control_plane=RuntimeControlPlane(store))
    record, _ = runtime.ensure_session(
        "session_1",
        core_id="assistant",
        core_revision="0001",
        channel="tui",
        conversation_key="local",
    )

    message = runtime.append_delivery_message(
        record.session_id,
        role="assistant",
        content="hello",
        turn_id="turn_1",
        delivery_id="delivery_1",
        channel="tui",
        target={"conversation_key": "local"},
        delivery_payload={"fallback_text": "hello"},
        delivery_idempotency_key="delivery_1",
    )

    events = store.query(
        RuntimeQuery(table="runtime_events", where={"idempotency_key": "delivery:delivery_1:message_outbox"}, order_by="seq")
    ).rows
    outbox = store.query(RuntimeQuery(table="outbox", where={"delivery_id": "delivery_1"})).rows[0]
    work = store.query(RuntimeQuery(table="runtime_work_items", where={"work_id": "delivery_1"}, limit=1)).rows[0]

    assert [event["type"] for event in events] == ["message.persisted", "delivery.queued", "work.enqueued"]
    assert events[0]["aggregate_id"] == message.id
    assert outbox["owner_turn_id"] == "turn_1"
    assert outbox["payload"]["message_id"] == message.id
    assert work["kind"] == "delivery.send"
    assert work["status"] == "queued"
    assert work["owner_turn_id"] == "turn_1"
    assert work["parent_work_id"] is None
    assert work["payload"]["owner_turn_id"] == "turn_1"
    assert work["payload"]["message_id"] == message.id


def test_session_runtime_uses_unique_conversation_binding(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    runtime = SessionRuntime(control_plane=RuntimeControlPlane(store))

    first, first_created = runtime.ensure_session(
        "",
        core_id="assistant",
        core_revision="0001",
        channel="telegram",
        conversation_key="chat_1",
    )
    second, second_created = runtime.ensure_session(
        "",
        core_id="assistant",
        core_revision="0001",
        channel="telegram",
        conversation_key="chat_1",
    )

    assert first_created is True
    assert second_created is False
    assert second.session_id == first.session_id
    assert len(runtime.list_sessions(core_id="assistant", limit=10)) == 1
    assert runtime.resolve_interaction_session(
        core_id="assistant",
        channel="telegram",
        conversation_key="chat_1",
    ) == first.session_id


def test_create_app_recovers_stale_delivery_work_once(tmp_path):
    home = tmp_path / "home"
    store = RuntimeStore.default(home)
    runtime = SessionRuntime(control_plane=RuntimeControlPlane(store))
    record, _ = runtime.ensure_session(
        "session_1",
        core_id="assistant",
        core_revision="0001",
        channel="tui",
        conversation_key="local",
    )
    runtime.append_delivery_message(
        record.session_id,
        role="assistant",
        content="hello",
        turn_id="turn_1",
        delivery_id="delivery_1",
        channel="tui",
        target={"conversation_key": "local"},
        delivery_payload={"fallback_text": "hello"},
        delivery_idempotency_key="delivery_1",
    )
    old_now = datetime(2000, 1, 1, tzinfo=UTC)
    work = DurableWorkRuntime(store)
    claim = work.claim("delivery_1", owner_id="delivery_worker", now=old_now, lease_seconds=1)
    assert claim is not None
    work.mark_sending(claim, now=old_now)

    app = create_app(home=home, provider_name="fake")
    create_app(home=home, provider_name="fake")

    work_row = app.runtime_store.query(RuntimeQuery(table="runtime_work_items", where={"work_id": "delivery_1"})).rows[0]
    outbox_row = app.runtime_store.query(RuntimeQuery(table="outbox", where={"delivery_id": "delivery_1"})).rows[0]
    unknown_events = app.runtime_store.query(
        RuntimeQuery(table="runtime_events", where={"type": "delivery.unknown", "aggregate_id": "delivery_1"})
    ).rows

    assert work_row["status"] == "unknown"
    assert work_row["claim_id"] is None
    assert outbox_row["status"] == "unknown"
    assert len(unknown_events) == 1
    assert app.runtime_recovery_summary["unknown"] == 1
    assert app.status()["runtime_recovery"]["unknown"] == 1


@pytest.mark.asyncio
async def test_runner_turn_projects_to_runtime_store(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    app.runner.provider = StaticProvider()

    result = await app.runner.run_turn("hello")

    session_rows = app.runtime_store.query(
        RuntimeQuery(table="sessions", where={"session_id": result.session_id}, limit=1)
    ).rows
    turn_rows = app.runtime_store.query(
        RuntimeQuery(table="turns", where={"turn_id": result.turn_id}, limit=1)
    ).rows
    message_rows = app.runtime_store.query(
        RuntimeQuery(table="messages", where={"session_id": result.session_id}, order_by="created_at", limit=10)
    ).rows
    outbox_rows = app.runtime_store.query(RuntimeQuery(table="outbox", where={"owner_turn_id": result.turn_id}, limit=10)).rows

    assert session_rows[0]["core_id"] == "assistant"
    with pytest.raises(KeyError, match="task not found"):
        app.control_plane.read(result.turn_id)
    assert app.task_worker.list_tasks(owner_session_id=result.session_id) == []
    assert turn_rows[0]["status"] == "completed"
    assert turn_rows[0]["task_id"] is None
    assert [row["role"] for row in message_rows] == ["user", "assistant"]
    assert message_rows[0]["content"]["text"] == "hello"
    assert message_rows[1]["content"]["text"] == "assistant reply"
    assert outbox_rows[0]["status"] == "queued"
    assert outbox_rows[0]["payload"]["fallback_text"] == "assistant reply"

    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    status_turn = TurnContext(
        session_id=result.session_id,
        turn_id=result.turn_id,
        core_id=core.core_id,
        core_revision=core.revision,
        user_input=AgentInput(content="", metadata={}),
        metadata={},
    )
    status = await app.runner.execute_tool(
        ToolCall(name="task_status", arguments={"task_id": result.turn_id}),
        core=core,
        turn=status_turn,
        capability=CapabilityFacade(core),
        emit_event=app.runner.event_log.emit,
    )
    control = await app.runner.execute_tool(
        ToolCall(name="task_control", arguments={"task_id": result.turn_id}),
        core=core,
        turn=status_turn,
        capability=CapabilityFacade(core),
        emit_event=app.runner.event_log.emit,
    )
    assert status.is_error is True
    assert control.is_error is True
    assert "background task not found" in status.content
    assert "background task not found" in control.content
    with pytest.raises(KeyError, match="task not found"):
        app.control_plane.read(result.turn_id)


@pytest.mark.asyncio
async def test_runner_marks_turn_failed_when_provider_raises(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    app.runner.provider = RaisingProvider()

    with pytest.raises(RuntimeError, match="provider exploded"):
        await app.runner.run_turn("hello")

    turns = app.runtime_store.query(RuntimeQuery(table="turns", order_by="created_at", limit=10)).rows
    failed_turn = turns[-1]

    assert failed_turn["status"] == "failed"
    with pytest.raises(KeyError, match="task not found"):
        app.control_plane.read(failed_turn["turn_id"])


@pytest.mark.asyncio
async def test_runner_marks_turn_cancelled_when_provider_turn_is_cancelled(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    provider = BlockingProvider()
    app.runner.provider = provider

    task = asyncio.create_task(app.runner.run_turn("hello"))
    await provider.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    turns = app.runtime_store.query(RuntimeQuery(table="turns", order_by="created_at", limit=10)).rows
    cancelled_turn = turns[-1]

    assert cancelled_turn["status"] == "cancelled"
    with pytest.raises(KeyError, match="task not found"):
        app.control_plane.read(cancelled_turn["turn_id"])


@pytest.mark.asyncio
async def test_runner_saves_local_agent_edits_before_turn(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    app.runner.provider = StaticProvider()
    original_revision = app.version_store.core_repository.live_revision()
    soul = app.version_store.active_core_path("assistant") / "agent" / "SOUL.md"
    soul.write_text(soul.read_text(encoding="utf-8") + "\n\nManual turn pre-edit.\n", encoding="utf-8")

    result = await app.runner.run_turn("hello")

    new_revision = app.version_store.core_repository.live_revision()
    assert new_revision != original_revision
    assert app.version_store.core_repository.live_changed_paths() == []
    session_rows = app.runtime_store.query(RuntimeQuery(table="sessions", where={"session_id": result.session_id}, limit=1)).rows
    assert session_rows[0]["target"]["core_revision"] == new_revision


@pytest.mark.asyncio
async def test_runner_tool_loop_projects_tool_calls_to_runtime_store(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    app.runner.provider = ToolThenAnswerProvider()

    await app.runner.run_turn("list tools")

    rows = app.runtime_store.query(RuntimeQuery(table="tool_calls", where={"call_id": "tools_1"}, limit=1)).rows

    assert rows[0]["turn_id"].startswith("turn_")
    assert rows[0]["tool_name"] == "tools_list"
    assert rows[0]["status"] == "succeeded"
    assert "tools" in rows[0]["result"]["data"]
