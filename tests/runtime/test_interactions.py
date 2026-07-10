from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from demiurge.app import create_app
from demiurge.providers import LLMResponse, ToolCall
from demiurge.runtime.interactions import (
    InteractionDelivery,
    InteractionExecutionRuntime,
    InteractionInbound,
    InteractionItem,
    InteractionResponseRuntime,
    InteractionRuntime,
    SessionRouteBinding,
)
from demiurge.sdk import ToolResult
from demiurge.tools.records import ToolExecutionRecord


class FakeBackgroundTasks:
    def __init__(self) -> None:
        self.drains: list[bool] = []

    async def drain(self, *, include_runtime_tasks: bool = True) -> None:
        self.drains.append(include_runtime_tasks)


class FakeRunner:
    def __init__(self, result=None) -> None:
        self.background_tasks = FakeBackgroundTasks()
        self.run_turn_calls = []
        self.result = result if result is not None else object()

    async def run_turn(self, text, *, interaction, route_binding):
        self.run_turn_calls.append(
            {
                "text": text,
                "interaction": interaction,
                "route_binding": route_binding,
            }
        )
        return self.result


class FakeRoute:
    async def deliver(self, outbound) -> None:
        return None

    async def prompt_user(self, prompt) -> str:
        return ""

    async def request_approval(self, request):
        raise AssertionError("approval should not be requested")


class CoordinatedSessionProvider:
    def __init__(self) -> None:
        self.calls = 0
        self.first_started = asyncio.Event()
        self.release_first = asyncio.Event()

    async def complete(self, request):
        self.calls += 1
        if self.calls == 1:
            self.first_started.set()
            await self.release_first.wait()
            return LLMResponse(content="assistant-A")
        self.release_first.set()
        return LLMResponse(content="assistant-B")


class RecordingSessionRoute:
    def __init__(self) -> None:
        self.outbounds = []

    async def deliver(self, outbound) -> None:
        self.outbounds.append(outbound)

    async def prompt_user(self, prompt):
        raise AssertionError("prompt should not be requested")

    async def request_approval(self, request):
        raise AssertionError("approval should not be requested")


def _tool_record(
    content: str,
    *,
    data: dict | None = None,
    call_id: str = "call_1",
) -> ToolExecutionRecord:
    return ToolExecutionRecord(
        call=ToolCall(name="clarify", arguments={}, id=call_id),
        result=ToolResult(content=content, data=data),
    )


@pytest.mark.asyncio
async def test_interaction_execution_runtime_runs_turn_and_drains_local_background_work():
    runner = FakeRunner(result={"ok": True})
    runtime = InteractionExecutionRuntime(runner)
    route = FakeRoute()
    inbound = InteractionInbound(
        channel="tui",
        text="hello",
        source="local",
        conversation_key="local:test",
    )

    result = await runtime.run(inbound, route=route)

    assert result == {"ok": True}
    assert runner.run_turn_calls == [
        {
            "text": "hello",
            "interaction": inbound,
            "route_binding": runner.run_turn_calls[0]["route_binding"],
        }
    ]
    route_binding = runner.run_turn_calls[0]["route_binding"]
    assert isinstance(route_binding, SessionRouteBinding)
    assert route_binding.route is route
    assert runner.background_tasks.drains == [False]


@pytest.mark.asyncio
async def test_interaction_execution_runtime_keeps_explicit_route_binding():
    runner = FakeRunner()
    runtime = InteractionExecutionRuntime(runner)
    route_binding = SessionRouteBinding(route=FakeRoute())
    inbound = InteractionInbound(channel="tui", text="hello", source="local")

    await runtime.run(inbound, route_binding=route_binding, route=FakeRoute())

    assert runner.run_turn_calls[0]["route_binding"] is route_binding


def test_interaction_response_runtime_builds_pending_outbound_and_prompt():
    pending = InteractionItem.delivery_item(InteractionDelivery(text="visible"))
    delivered = InteractionItem.delivery_item(
        InteractionDelivery(text="already sent", metadata={"delivery_status": "delivered"})
    )
    older_record = _tool_record("older", data={"needs_user": True, "question": "older?"}, call_id="call_old")
    latest_record = _tool_record(
        "fallback question",
        data={"needs_user": True, "choices": [1, "two"]},
        call_id="call_latest",
    )
    result = SimpleNamespace(
        session_id="session_1",
        turn_id="turn_1",
        items=[pending, delivered],
        needs_user=True,
        tool_results=[older_record, latest_record],
    )
    inbound = InteractionInbound(
        channel="telegram",
        text="hello",
        source="123",
        reply_to="456",
        conversation_key="telegram:dm:123",
        metadata={"platform": "telegram"},
    )

    outbound = InteractionResponseRuntime().build(result, inbound)

    assert outbound.channel == "telegram"
    assert outbound.session_id == "session_1"
    assert outbound.turn_id == "turn_1"
    assert outbound.items == [pending]
    assert outbound.metadata == {
        "source": "123",
        "reply_to": "456",
        "conversation_key": "telegram:dm:123",
        "platform": "telegram",
    }
    assert outbound.prompt is not None
    assert outbound.prompt.question == "fallback question"
    assert outbound.prompt.choices == ["1", "two"]
    assert outbound.prompt.session_id == "session_1"
    assert outbound.prompt.turn_id == "turn_1"
    assert outbound.prompt.conversation_key == "telegram:dm:123"
    assert outbound.prompt.metadata == {
        "channel": "telegram",
        "source": "123",
        "reply_to": "456",
        "platform": "telegram",
    }


def test_interaction_response_runtime_omits_prompt_without_needs_user():
    result = SimpleNamespace(
        session_id="session_1",
        turn_id="turn_1",
        items=[],
        needs_user=False,
        tool_results=[_tool_record("ignored", data={"needs_user": True})],
    )
    inbound = InteractionInbound(channel="tui", text="hello", source="local")

    outbound = InteractionResponseRuntime().build(result, inbound)

    assert outbound.prompt is None


@pytest.mark.asyncio
async def test_interaction_runtime_facade_runs_execution_then_response_projection():
    item = InteractionItem.delivery_item(InteractionDelivery(text="done"))
    result = SimpleNamespace(
        session_id="session_1",
        turn_id="turn_1",
        items=[item],
        needs_user=False,
        tool_results=[],
    )
    runner = FakeRunner(result=result)
    runtime = InteractionRuntime(runner)
    inbound = InteractionInbound(channel="tui", text="hello", source="local")

    outbound = await runtime.handle(inbound)

    assert runner.run_turn_calls[0]["text"] == "hello"
    assert runner.background_tasks.drains == [False]
    assert outbound.items == [item]
    assert outbound.session_id == "session_1"


@pytest.mark.asyncio
async def test_ses_01_concurrent_turns_keep_assistant_history_isolated_by_session(tmp_path):
    """SES-01: concurrent interaction turns keep assistant history session-local."""
    app = create_app(home=tmp_path / "home", provider_name="fake")
    provider = CoordinatedSessionProvider()
    app.runner.provider = provider
    runtime = InteractionRuntime(app.runner)
    inbound_a = InteractionInbound(
        channel="probe",
        text="user-A",
        source="A",
        conversation_key="probe:dm:A",
    )
    inbound_b = InteractionInbound(
        channel="probe",
        text="user-B",
        source="B",
        conversation_key="probe:dm:B",
    )
    route_a = RecordingSessionRoute()
    route_b = RecordingSessionRoute()
    task_a = asyncio.create_task(runtime.handle(inbound_a, route=route_a))

    try:
        await asyncio.wait_for(provider.first_started.wait(), timeout=5)
        outbound_b = await asyncio.wait_for(runtime.handle(inbound_b, route=route_b), timeout=5)
        outbound_a = await asyncio.wait_for(task_a, timeout=5)

        def history(session_id: str) -> list[tuple[str, str]]:
            return [
                (message.role, message.content)
                for message in app.session_runtime.read_messages(session_id)
            ]

        assert outbound_a.session_id != outbound_b.session_id
        assert {
            "A": {
                "history": history(outbound_a.session_id),
                "deliveries": [
                    (outbound.session_id, [delivery.text for delivery in outbound.deliveries])
                    for outbound in route_a.outbounds
                ],
                "conversation_key": outbound_a.metadata["conversation_key"],
            },
            "B": {
                "history": history(outbound_b.session_id),
                "deliveries": [
                    (outbound.session_id, [delivery.text for delivery in outbound.deliveries])
                    for outbound in route_b.outbounds
                ],
                "conversation_key": outbound_b.metadata["conversation_key"],
            },
        } == {
            "A": {
                "history": [("user", "user-A"), ("assistant", "assistant-A")],
                "deliveries": [(outbound_a.session_id, ["assistant-A"])],
                "conversation_key": "probe:dm:A",
            },
            "B": {
                "history": [("user", "user-B"), ("assistant", "assistant-B")],
                "deliveries": [(outbound_b.session_id, ["assistant-B"])],
                "conversation_key": "probe:dm:B",
            },
        }
    finally:
        if not task_a.done():
            task_a.cancel()
            await asyncio.gather(task_a, return_exceptions=True)
        await app.close()
