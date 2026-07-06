import asyncio
import shutil

import pytest
import yaml

pytestmark = pytest.mark.slow_integration

from demiurge.app import create_app, source_agents_root
from demiurge.providers import LLMResponse, ToolCall
from demiurge.runtime.delegation import subagents_command_text
from demiurge.sdk import AgentInput, TurnContext
from demiurge.security.capabilities import CapabilityFacade
from demiurge.slash import specs_for_surface
from demiurge.tools.registry import BUILTIN_TOOL_DEFINITIONS


CHILD_AGENT_COMPLETION_TIMEOUT = 20


class StaticProvider:
    async def complete(self, request):
        return LLMResponse(content="child done")


class RecordingProvider:
    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.requests = []

    async def complete(self, request):
        self.requests.append(request)
        if self.responses:
            item = self.responses.pop(0)
            if isinstance(item, LLMResponse):
                return item
            return LLMResponse(content=str(item))
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
        metadata={},
    )


def _without_default_capability(core, name: str):
    defaults = core.raw_manifest.setdefault("capabilities", {}).setdefault("defaults", {})
    defaults.pop(name, None)
    return core


def _copy_agents(tmp_path):
    target = tmp_path / "agents"
    shutil.copytree(source_agents_root(), target)
    return target


def _write_module(root, rel_path, code):
    path = root / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(code, encoding="utf-8")


def _write_slot(root, rel_path, text=None):
    path = root / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        text
        or "\n".join(
            [
                "entrypoint: module:process",
                "description: test slot",
                "failure_policy: soft",
                "capabilities:",
                "  []",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def _write_pipeline(root, phase, *, serial=None, parallel=None, core_id="assistant"):
    path = root / core_id / "agent" / "pipelines.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw[phase] = {"serial": list(serial or [])}
    if phase != "bootstrap":
        raw[phase]["parallel"] = list(parallel or [])
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")


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
        ToolCall(
            name="yield_until",
            arguments={"task_id": task_id, "timeout_seconds": CHILD_AGENT_COMPLETION_TIMEOUT},
        ),
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
async def test_delegate_task_defaults_to_base_slots_and_records_metadata(tmp_path):
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
    await app.task_worker.wait(delegated.data["task_id"], timeout_seconds=CHILD_AGENT_COMPLETION_TIMEOUT)

    record = app.task_worker.get(delegated.data["task_id"])
    assert record.metadata["requested_child_agent_slots"] == {
        "input_slots": ["base_input"],
        "output_slots": ["base_output"],
        "use_bootstrap": False,
    }
    assert record.metadata["resolved_child_agent_slots"] == {
        "input_slots": {"serial": ["base_input"], "parallel": []},
        "output_slots": {"serial": ["base_output"], "parallel": []},
        "use_bootstrap": False,
    }
    assert record.metadata["requested_child_agent_tools"] == "all"
    assert "read_file" in record.metadata["resolved_child_agent_tools"]["resolved"]


@pytest.mark.asyncio
async def test_delegate_task_all_slots_preserves_child_pipeline_metadata(tmp_path):
    agents = _copy_agents(tmp_path)
    _write_module(
        agents,
        "evolver/agent/output/child_extra/module.py",
        "def process(ctx):\n"
        "    ctx.output.send_text('child-extra')\n",
    )
    _write_slot(agents, "evolver/agent/output/child_extra/slot.yaml")
    _write_pipeline(agents, "output", serial=["base_output", "child_extra"], core_id="evolver")
    app = create_app(home=tmp_path / "home", provider_name="fake", agents_root=agents)
    app.runner.provider = StaticProvider()
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    turn = _turn(app, core)
    capability = CapabilityFacade(core)

    delegated = await app.runner.execute_tool(
        ToolCall(
            name="delegate_task",
            arguments={
                "goal": "do child work",
                "core_id": "evolver",
                "input_slots": "all",
                "output_slots": "all",
            },
        ),
        core=core,
        turn=turn,
        capability=capability,
        emit_event=app.runner.event_log.emit,
    )
    await app.task_worker.wait(delegated.data["task_id"], timeout_seconds=CHILD_AGENT_COMPLETION_TIMEOUT)

    record = app.task_worker.get(delegated.data["task_id"])
    assert record.metadata["requested_child_agent_slots"] == {
        "input_slots": "all",
        "output_slots": "all",
        "use_bootstrap": False,
    }
    assert record.metadata["resolved_child_agent_slots"]["output_slots"] == {
        "serial": ["base_output", "child_extra"],
        "parallel": [],
    }


@pytest.mark.asyncio
async def test_delegate_task_returns_tool_error_for_invalid_child_slot_selection(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    turn = _turn(app, core)
    capability = CapabilityFacade(core)

    result = await app.runner.execute_tool(
        ToolCall(
            name="delegate_task",
            arguments={"goal": "do child work", "core_id": "evolver", "input_slots": ["missing"]},
        ),
        core=core,
        turn=turn,
        capability=capability,
        emit_event=app.runner.event_log.emit,
    )

    assert result.is_error is True
    assert "unknown input slot id: missing" in result.content


def test_delegate_task_schema_exposes_tools_selection_not_tool_policy():
    schema = BUILTIN_TOOL_DEFINITIONS["delegate_task"].input_schema
    properties = schema["properties"]

    assert "tool_policy" not in properties
    assert properties["tools"] == {
        "anyOf": [
            {"type": "string", "enum": ["all", "none"]},
            {"type": "array", "items": {"type": "string"}},
        ],
        "default": "all",
    }


@pytest.mark.asyncio
async def test_delegate_task_returns_tool_error_for_invalid_child_tool_selection(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    turn = _turn(app, core)
    capability = CapabilityFacade(core)

    result = await app.runner.execute_tool(
        ToolCall(
            name="delegate_task",
            arguments={"goal": "do child work", "core_id": "evolver", "tools": ["missing"]},
        ),
        core=core,
        turn=turn,
        capability=capability,
        emit_event=app.runner.event_log.emit,
    )

    assert result.is_error is True
    assert "unknown child tool id: missing" in result.content


@pytest.mark.asyncio
async def test_delegate_task_rejects_legacy_tool_policy_argument(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    turn = _turn(app, core)
    capability = CapabilityFacade(core)

    result = await app.runner.execute_tool(
        ToolCall(
            name="delegate_task",
            arguments={"goal": "do child work", "core_id": "evolver", "tool_policy": {"deny": ["read_file"]}},
        ),
        core=core,
        turn=turn,
        capability=capability,
        emit_event=app.runner.event_log.emit,
    )

    assert result.is_error is True
    assert "tool_policy is not supported; use tools" in result.content


@pytest.mark.asyncio
async def test_delegate_task_passes_child_tool_selection_to_spawned_task(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    provider = RecordingProvider()
    app.runner.provider = provider
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    turn = _turn(app, core)
    capability = CapabilityFacade(core)

    delegated = await app.runner.execute_tool(
        ToolCall(
            name="delegate_task",
            arguments={"goal": "do child work", "core_id": "evolver", "tools": ["read_file"]},
        ),
        core=core,
        turn=turn,
        capability=capability,
        emit_event=app.runner.event_log.emit,
    )
    await app.task_worker.wait(delegated.data["task_id"], timeout_seconds=CHILD_AGENT_COMPLETION_TIMEOUT)

    record = app.task_worker.get(delegated.data["task_id"])
    assert record.metadata["requested_child_agent_tools"] == ["read_file"]
    assert record.metadata["resolved_child_agent_tools"] == {
        "requested": ["read_file"],
        "resolved": ["read_file"],
    }
    assert [tool.name for tool in provider.requests[0].tools] == ["read_file"]


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
    await app.runner.background_tasks.drain()
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
    await app.task_worker.wait(delegated.data["task_id"], timeout_seconds=CHILD_AGENT_COMPLETION_TIMEOUT)

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
    await app.runner.background_tasks.drain()


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
async def test_delegate_task_rejects_depth_excess_and_accepts_tool_selection(tmp_path):
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
    tool_selection = await app.runner.execute_tool(
        ToolCall(
            name="delegate_task",
            arguments={"goal": "nested", "core_id": "evolver", "tools": "none"},
        ),
        core=core,
        turn=_turn(app, core),
        capability=capability,
        emit_event=app.runner.event_log.emit,
    )

    assert too_deep.is_error is True
    assert "max_depth=2" in too_deep.content
    assert tool_selection.is_error is False
    record = app.task_worker.get(tool_selection.data["task_id"])
    assert record.metadata["requested_child_agent_tools"] == "none"


@pytest.mark.asyncio
async def test_tool_policy_denies_child_tool_execution(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    turn = _turn(app, core)
    turn.metadata["tool_policy"] = {"deny": ["read_file"]}
    capability = CapabilityFacade(core)

    result = await app.tool_runtime.execute(
        ToolCall(name="read_file", arguments={}),
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
