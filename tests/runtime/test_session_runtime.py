import pytest

from demiurge.app import create_app
from demiurge.providers import LLMResponse, ToolCall
from demiurge.runtime.control import RuntimeControlPlane
from demiurge.runtime.session import SessionQuery, SessionRuntime
from demiurge.runtime.store import RuntimeQuery, RuntimeStore


class StaticProvider:
    async def complete(self, request):
        return LLMResponse(content="assistant reply")


class ToolThenAnswerProvider:
    def __init__(self):
        self.calls = 0

    async def complete(self, request):
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(tool_calls=[ToolCall(id="tools_1", name="tools_list", arguments={})])
        return LLMResponse(content="tool complete")


def test_session_runtime_projects_session_turn_and_messages(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    runtime = SessionRuntime(control_plane=RuntimeControlPlane(store))

    record, created = runtime.ensure_session(
        "session_1",
        core_id="assistant",
        core_version="0001",
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
        core_version="0001",
        channel="tui",
        conversation_key="local",
    )

    message = runtime.append_delivery_message(
        record.session_id,
        role="assistant",
        content="hello",
        turn_id="turn_1",
        delivery_id="delivery_1",
        task_id="turn_1",
        channel="tui",
        target={"conversation_key": "local"},
        delivery_payload={"fallback_text": "hello"},
        delivery_idempotency_key="delivery_1",
    )

    events = store.query(
        RuntimeQuery(table="runtime_events", where={"idempotency_key": "delivery:delivery_1:message_outbox"}, order_by="seq")
    ).rows
    outbox = store.query(RuntimeQuery(table="outbox", where={"delivery_id": "delivery_1"})).rows[0]

    assert [event["type"] for event in events] == ["message.persisted", "delivery.queued"]
    assert events[0]["aggregate_id"] == message.id
    assert outbox["payload"]["message_id"] == message.id


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
    outbox_rows = app.runtime_store.query(RuntimeQuery(table="outbox", where={"task_id": result.turn_id}, limit=10)).rows

    assert session_rows[0]["core_id"] == "assistant"
    assert app.control_plane.read(result.turn_id)["kind"] == "agent.turn"
    assert app.control_plane.read(result.turn_id)["status"] == "succeeded"
    assert turn_rows[0]["status"] == "completed"
    assert turn_rows[0]["task_id"] == result.turn_id
    assert [row["role"] for row in message_rows] == ["user", "assistant"]
    assert message_rows[0]["content"]["text"] == "hello"
    assert message_rows[1]["content"]["text"] == "assistant reply"
    assert outbox_rows[0]["status"] == "queued"
    assert outbox_rows[0]["payload"]["fallback_text"] == "assistant reply"


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
