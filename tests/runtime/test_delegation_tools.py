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


def _turn(app, core):
    return TurnContext(
        session_id=app.runner.session_id,
        turn_id="turn_delegate",
        core_id=core.core_id,
        core_version=core.version,
        user_input=AgentInput(content="delegate", metadata={}),
        state={},
        metadata={},
    )


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

    assert status.data["job_id"] == task_id
    assert waited.data["status"] == "succeeded"
    assert app.control_plane.read(task_id)["kind"] == "agent.spawn"
    listing = await subagents_command_text(app.task_worker, session_id=app.runner.session_id, args="")
    detail = await subagents_command_text(app.task_worker, session_id=app.runner.session_id, args=task_id)
    assert task_id in listing
    assert "Subagent" in detail


@pytest.mark.asyncio
async def test_run_terminal_defaults_to_background_task(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake", workspace=tmp_path)
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    turn = _turn(app, core)
    capability = CapabilityFacade(core)

    result = await app.tool_runtime.execute(
        ToolCall(name="run_terminal", arguments={"command": "printf ok"}),
        core=core,
        turn=turn,
        capability=capability,
        emit_event=app.runner.event_log.emit,
    )

    assert result.is_error is False
    assert result.data["executionStarted"] is True
    assert result.data["job_id"].startswith("job_")
    await app.task_worker.wait(result.data["job_id"], timeout_seconds=2)
    assert app.control_plane.read(result.data["job_id"])["kind"] == "terminal.exec"


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
    assert tool_policy.data["tool_policy"] == {"deny": ["terminal"]}


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
