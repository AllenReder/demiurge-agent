import asyncio

import pytest

from demiurge.app import create_app
from demiurge.providers import LLMResponse, ToolCall
from demiurge.runtime.delegation import subagents_command_text
from demiurge.sdk import AgentInput, TurnContext
from demiurge.security.capabilities import CapabilityFacade
from demiurge.slash import specs_for_surface


class StaticProvider:
    async def complete(self, request):
        return LLMResponse(content="child done")


class BlockingProvider:
    def __init__(self):
        self.started = 0
        self.release = None

    async def complete(self, request):
        self.started += 1
        if self.release is None:
            self.release = asyncio.Event()
        await self.release.wait()
        return LLMResponse(content="child done")


def _turn(app, core):
    return TurnContext(
        session_id=app.runner.session_id,
        turn_id="turn_delegate",
        core_id=core.core_id,
        core_revision=core.revision,
        user_input=AgentInput(content="delegate", metadata={}),
        state={},
        metadata={},
    )


def _without_default_capability(core, name: str):
    defaults = core.raw_manifest.setdefault("capabilities", {}).setdefault("defaults", {})
    defaults.pop(name, None)
    return core


@pytest.mark.asyncio
async def test_delegate_task_status_and_yield_until_use_runtime_tasks(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    app.runner.provider = StaticProvider()
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    turn = _turn(app, core)
    capability = CapabilityFacade(core)

    delegated = await app.runner.execute_tool(
        ToolCall(name="delegate_task", arguments={"goal": "do child work", "core_id": "evolver"}),
        core=core,
        turn=turn,
        capability=capability,
        emit_event=app.runner.event_log.emit,
    )

    assert delegated.is_error is False
    assert set(delegated.data) == {"task_id"}
    task_id = delegated.data["task_id"]
    status = await app.runner.execute_tool(
        ToolCall(name="task_status", arguments={"task_id": task_id}),
        core=core,
        turn=turn,
        capability=capability,
        emit_event=app.runner.event_log.emit,
    )
    waited = await app.runner.execute_tool(
        ToolCall(name="yield_until", arguments={"task_id": task_id, "timeout_seconds": 2}),
        core=core,
        turn=turn,
        capability=capability,
        emit_event=app.runner.event_log.emit,
    )

    assert status.data["task_id"] == task_id
    assert waited.data["status"] == "succeeded"
    assert app.control_plane.read(task_id)["kind"] == "agent.spawn"
    listing = await subagents_command_text(app.task_worker, session_id=app.runner.session_id, args="")
    detail = await subagents_command_text(app.task_worker, session_id=app.runner.session_id, args=task_id)
    assert task_id in listing
    assert "Subagent" in detail


@pytest.mark.asyncio
async def test_runtime_task_wait_can_consume_existing_completion_event(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")

    async def task(ctx):
        ctx.append_log("finished")
        return "worker done"

    record = app.task_worker.start_task(
        kind="terminal.exec",
        owner_session_id=app.runner.session_id,
        owner_turn_id="turn_origin",
        source_tool="test",
        task_factory=task,
    )

    await app.task_worker.wait(record.task_id, timeout_seconds=1)
    assert [event.task_id for event in app.task_worker.pending_events_for_session(app.runner.session_id)] == [
        record.task_id
    ]

    waited = await app.task_worker.wait(record.task_id, timeout_seconds=1, consume_completion=True)

    assert waited.status == "succeeded"
    assert app.task_worker.pending_events_for_session(app.runner.session_id) == []


@pytest.mark.asyncio
async def test_yield_until_timeout_returns_running_status_without_tool_error(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    provider = BlockingProvider()
    app.runner.provider = provider
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    turn = _turn(app, core)
    capability = CapabilityFacade(core)

    delegated = await app.runner.execute_tool(
        ToolCall(name="delegate_task", arguments={"goal": "do slow child work", "core_id": "evolver"}),
        core=core,
        turn=turn,
        capability=capability,
        emit_event=app.runner.event_log.emit,
    )
    task_id = delegated.data["task_id"]
    for _ in range(50):
        if provider.started and provider.release is not None:
            break
        await asyncio.sleep(0.01)
    assert provider.release is not None

    waited = await app.runner.execute_tool(
        ToolCall(name="yield_until", arguments={"task_id": task_id, "timeout_seconds": 0.01}),
        core=core,
        turn=turn,
        capability=capability,
        emit_event=app.runner.event_log.emit,
    )

    assert waited.is_error is False
    assert waited.data["task_id"] == task_id
    assert waited.data["status"] == "running"
    assert waited.data["running"] is True
    assert waited.data["timed_out"] is True
    provider.release.set()
    await app.runner.drain_background_tasks()
    assert [event.task_id for event in app.task_worker.pending_events_for_session(app.runner.session_id)] == [
        task_id
    ]


@pytest.mark.asyncio
async def test_delegation_tools_require_capabilities(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    turn = _turn(app, core)

    no_spawn = _without_default_capability(core, "agents.spawn:evolver")
    spawn = await app.runner.execute_tool(
        ToolCall(name="delegate_task", arguments={"goal": "do child work", "core_id": "evolver"}),
        core=no_spawn,
        turn=turn,
        capability=CapabilityFacade(no_spawn),
        emit_event=app.runner.event_log.emit,
    )

    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    no_task_control = _without_default_capability(core, "task.control")
    status = await app.runner.execute_tool(
        ToolCall(name="task_status", arguments={"task_id": "task_missing"}),
        core=no_task_control,
        turn=turn,
        capability=CapabilityFacade(no_task_control),
        emit_event=app.runner.event_log.emit,
    )
    control = await app.runner.execute_tool(
        ToolCall(name="task_control", arguments={"task_id": "task_missing"}),
        core=no_task_control,
        turn=turn,
        capability=CapabilityFacade(no_task_control),
        emit_event=app.runner.event_log.emit,
    )
    waited = await app.runner.execute_tool(
        ToolCall(name="yield_until", arguments={"task_id": "task_missing"}),
        core=no_task_control,
        turn=turn,
        capability=CapabilityFacade(no_task_control),
        emit_event=app.runner.event_log.emit,
    )

    assert spawn.is_error is True
    assert "capability denied: agents.spawn:evolver" in spawn.content
    assert status.is_error is True
    assert control.is_error is True
    assert waited.is_error is True
    assert "capability denied: task.control" in status.content
    assert "capability denied: task.control" in control.content
    assert "capability denied: task.control" in waited.content


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["retry", "handoff", "mute", "notify"])
async def test_task_control_rejects_unsupported_commands(tmp_path, command):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    turn = _turn(app, core)
    capability = CapabilityFacade(core)

    result = await app.runner.execute_tool(
        ToolCall(name="task_control", arguments={"task_id": "task_missing", "command": command}),
        core=core,
        turn=turn,
        capability=capability,
        emit_event=app.runner.event_log.emit,
    )

    assert result.is_error is True
    assert f"unsupported task_control command: {command}" in result.content


@pytest.mark.asyncio
async def test_delegate_task_silent_notify_policy_suppresses_completion_event(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    app.runner.provider = StaticProvider()
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    turn = _turn(app, core)
    capability = CapabilityFacade(core)

    delegated = await app.runner.execute_tool(
        ToolCall(
            name="delegate_task",
            arguments={"goal": "do quiet child work", "core_id": "evolver", "notify_policy": "silent"},
        ),
        core=core,
        turn=turn,
        capability=capability,
        emit_event=app.runner.event_log.emit,
    )
    await app.task_worker.wait(delegated.data["task_id"], timeout_seconds=2)

    assert delegated.is_error is False
    assert set(delegated.data) == {"task_id"}
    assert app.task_worker.pending_events_for_session(app.runner.session_id) == []


@pytest.mark.asyncio
async def test_delegate_task_rejects_unknown_notify_policy(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    turn = _turn(app, core)
    capability = CapabilityFacade(core)

    result = await app.runner.execute_tool(
        ToolCall(
            name="delegate_task",
            arguments={"goal": "do child work", "core_id": "evolver", "notify_policy": "notify"},
        ),
        core=core,
        turn=turn,
        capability=capability,
        emit_event=app.runner.event_log.emit,
    )

    assert result.is_error is True
    assert "unsupported notify_policy" in result.content


@pytest.mark.asyncio
async def test_delegate_task_allows_two_concurrent_children(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    provider = BlockingProvider()
    app.runner.provider = provider
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    turn = _turn(app, core)
    capability = CapabilityFacade(core)

    first = await app.runner.execute_tool(
        ToolCall(name="delegate_task", arguments={"goal": "first child", "core_id": "evolver"}),
        core=core,
        turn=turn,
        capability=capability,
        emit_event=app.runner.event_log.emit,
    )
    second = await app.runner.execute_tool(
        ToolCall(name="delegate_task", arguments={"goal": "second child", "core_id": "evolver"}),
        core=core,
        turn=turn,
        capability=capability,
        emit_event=app.runner.event_log.emit,
    )

    assert set(first.data) == {"task_id"}
    assert set(second.data) == {"task_id"}
    assert len(app.task_worker.list_tasks(owner_session_id=turn.session_id, kind="agent.spawn", include_completed=False)) == 2
    for _ in range(50):
        if provider.started >= 2 and provider.release is not None:
            break
        await asyncio.sleep(0.01)
    assert provider.started == 2
    assert provider.release is not None
    provider.release.set()
    await app.runner.drain_background_tasks()


@pytest.mark.asyncio
async def test_terminal_background_true_returns_runtime_task(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake", workspace=tmp_path)
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    turn = _turn(app, core)
    capability = CapabilityFacade(core)

    result = await app.tool_runtime.execute(
        ToolCall(name="terminal", arguments={"command": "printf ok", "background": True}),
        core=core,
        turn=turn,
        capability=capability,
        emit_event=app.runner.event_log.emit,
    )

    assert result.is_error is False
    assert set(result.data) == {"task_id"}
    assert result.data["task_id"].startswith("task_")
    await app.task_worker.wait(result.data["task_id"], timeout_seconds=2)
    assert app.control_plane.read(result.data["task_id"])["kind"] == "terminal.exec"


@pytest.mark.asyncio
async def test_delegate_task_rejects_depth_excess_and_accepts_tool_policy(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    turn = _turn(app, core)
    turn.metadata["delegation_depth"] = 2
    capability = CapabilityFacade(core)

    too_deep = await app.runner.execute_tool(
        ToolCall(name="delegate_task", arguments={"goal": "nested", "core_id": "evolver"}),
        core=core,
        turn=turn,
        capability=capability,
        emit_event=app.runner.event_log.emit,
    )
    tool_policy = await app.runner.execute_tool(
        ToolCall(
            name="delegate_task",
            arguments={"goal": "nested", "core_id": "evolver", "tool_policy": {"deny": ["terminal"]}},
        ),
        core=core,
        turn=_turn(app, core),
        capability=capability,
        emit_event=app.runner.event_log.emit,
    )

    assert too_deep.is_error is True
    assert "max_depth=2" in too_deep.content
    assert tool_policy.is_error is False
    record = app.task_worker.get(tool_policy.data["task_id"])
    assert record.metadata["tool_policy"] == {"deny": ["terminal"]}


@pytest.mark.asyncio
async def test_tool_policy_denies_child_tool_execution(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    turn = _turn(app, core)
    turn.metadata["tool_policy"] = {"deny": ["tools_list"]}
    capability = CapabilityFacade(core)

    result = await app.tool_runtime.execute(
        ToolCall(name="tools_list", arguments={}),
        core=core,
        turn=turn,
        capability=capability,
        emit_event=app.runner.event_log.emit,
    )

    assert result.is_error is True
    assert "not allowed" in result.content


def test_subagents_slash_command_is_available_on_tui_and_telegram():
    assert "subagents" in {spec.name for spec in specs_for_surface("tui")}
    assert "subagents" in {spec.name for spec in specs_for_surface("telegram")}
