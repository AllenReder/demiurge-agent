from __future__ import annotations

from types import SimpleNamespace

import pytest

from demiurge.core import AgentInfo, CoreManifest, LoadedCore
from demiurge.providers import ToolCall
from demiurge.runtime.delegation_tools import DelegationToolRuntime
from demiurge.sdk import AgentInput, ToolResult, TurnContext
from demiurge.security.capabilities import CapabilityFacade


def _core(*, task_control: bool = True) -> LoadedCore:
    defaults = {"task.control": True} if task_control else {}
    capabilities = {"defaults": defaults}
    manifest = CoreManifest(agent=AgentInfo(id="assistant"), capabilities=capabilities)
    return LoadedCore(
        root=None,
        manifest_path=None,
        manifest=manifest,
        raw_manifest=manifest.model_dump(),
        soul="",
        bootstrap_slots=[],
        bootstrap_pipeline=None,
        bootstrap_enabled=False,
        input_slots=[],
        output_slots=[],
        input_pipeline=None,
        output_pipeline=None,
        tool_slots=[],
        skills=[],
        schedules=[],
        mcp_servers=[],
    )


def _turn() -> TurnContext:
    return TurnContext(
        session_id="session_1",
        turn_id="turn_1",
        core_id="assistant",
        core_revision="rev_1",
        user_input=AgentInput(content="delegate"),
    )


def _scope():
    return SimpleNamespace(session_id="session_1")


class _ChildAgents:
    def __init__(self):
        self.calls = []

    async def handle_delegate_task(self, call, *, core, turn, capability):
        self.calls.append((call, core, turn, capability))
        return ToolResult(content='{"task_id":"task_1"}', data={"task_id": "task_1"})


class _TaskWorker:
    def get_owned(self, scope, task_id):
        raise KeyError(task_id)

    def log_owned(self, scope, task_id):
        return []

    def get(self, task_id):
        raise KeyError(task_id)

    def log(self, task_id):
        return []

    async def cancel(self, task_id):
        raise KeyError(task_id)

    async def cancel_owned(self, scope, task_id):
        raise KeyError(task_id)

    async def wait(self, task_id, *, timeout_seconds, consume_completion):
        raise KeyError(task_id)

    async def wait_owned(
        self,
        scope,
        task_id,
        *,
        timeout_seconds,
        consume_completion,
    ):
        raise KeyError(task_id)


class _Host:
    def __init__(self):
        self.child_agents = _ChildAgents()
        self.task_worker = _TaskWorker()


@pytest.mark.asyncio
async def test_delegation_adapter_does_not_repeat_visibility_resolution():
    host = _Host()
    runtime = DelegationToolRuntime(host)
    core = _core()

    result = await runtime.execute(
        ToolCall(name="task_status", arguments={"task_id": "task_1"}),
        core=core,
        turn=_turn(),
        capability=CapabilityFacade(core),
        principal_scope=_scope(),
    )

    assert result.is_error is True
    assert result.content == "background task not found: task_1"


@pytest.mark.asyncio
async def test_delegation_runtime_rejects_unsupported_tool():
    host = _Host()
    runtime = DelegationToolRuntime(host)
    core = _core()

    result = await runtime.execute(
        ToolCall(name="unsupported_builtin", arguments={}),
        core=core,
        turn=_turn(),
        capability=CapabilityFacade(core),
        principal_scope=_scope(),
    )

    assert result.is_error is True
    assert result.content == "unsupported delegation tool: unsupported_builtin"


@pytest.mark.asyncio
async def test_delegation_runtime_dispatches_delegate_task_to_child_agent_runtime():
    host = _Host()
    runtime = DelegationToolRuntime(host)
    core = _core()
    call = ToolCall(name="delegate_task", arguments={"goal": "work"})

    result = await runtime.execute(
        call,
        core=core,
        turn=_turn(),
        capability=CapabilityFacade(core),
        principal_scope=_scope(),
    )

    assert result.data == {"task_id": "task_1"}
    assert host.child_agents.calls[0][0] is call


@pytest.mark.asyncio
async def test_task_status_requires_task_id():
    host = _Host()
    runtime = DelegationToolRuntime(host)
    core = _core()

    result = await runtime.execute(
        ToolCall(name="task_status", arguments={}),
        core=core,
        turn=_turn(),
        capability=CapabilityFacade(core),
        principal_scope=_scope(),
    )

    assert result.is_error is True
    assert result.content == "task_id is required"


@pytest.mark.asyncio
async def test_task_control_rejects_unsupported_command_before_lookup():
    host = _Host()
    runtime = DelegationToolRuntime(host)
    core = _core()

    result = await runtime.execute(
        ToolCall(name="task_control", arguments={"task_id": "task_missing", "command": "retry"}),
        core=core,
        turn=_turn(),
        capability=CapabilityFacade(core),
        principal_scope=_scope(),
    )

    assert result.is_error is True
    assert result.content == "unsupported task_control command: retry"


@pytest.mark.asyncio
async def test_yield_until_missing_task_returns_model_facing_error():
    host = _Host()
    runtime = DelegationToolRuntime(host)
    core = _core()

    result = await runtime.execute(
        ToolCall(name="yield_until", arguments={"task_id": "task_missing", "timeout_seconds": 0}),
        core=core,
        turn=_turn(),
        capability=CapabilityFacade(core),
        principal_scope=_scope(),
    )

    assert result.is_error is True
    assert result.content == "background task not found: task_missing"


@pytest.mark.asyncio
async def test_delegation_adapter_does_not_repeat_capability_preflight():
    host = _Host()
    runtime = DelegationToolRuntime(host)
    core = _core(task_control=False)

    result = await runtime.execute(
        ToolCall(name="task_status", arguments={"task_id": "task_1"}),
        core=core,
        turn=_turn(),
        capability=CapabilityFacade(core),
        principal_scope=_scope(),
    )

    assert result.is_error is True
    assert result.content == "background task not found: task_1"
