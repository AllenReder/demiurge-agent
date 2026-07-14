import shutil

import pytest
import yaml

pytestmark = pytest.mark.slow_integration

from demiurge.app import create_app, source_agents_root
from demiurge.providers import ToolCall
from demiurge.sdk import AgentInput, TurnContext
from demiurge.security.capabilities import CapabilityFacade


def _copy_agents(tmp_path):
    target = tmp_path / "agents"
    shutil.copytree(source_agents_root(), target)
    return target


def _write_module(root, rel_path, code):
    path = root / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(code, encoding="utf-8")


def _write_slot(root, rel_path):
    path = root / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
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


def _turn(app, core, *, metadata=None):
    return TurnContext(
        session_id=app.runner.session_id,
        turn_id="turn_child_agents",
        core_id=core.core_id,
        core_revision=core.revision,
        user_input=AgentInput(content="delegate", metadata={}),
        metadata=dict(metadata or {}),
    )


def test_child_agent_slots_default_to_base_slots_and_all_preserves_pipeline(tmp_path):
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
    core = app.core_loader.load(app.version_store.active_core_path("evolver"))

    defaults = app.runner.child_agents.resolve_slots(
        core,
        input_slots=None,
        output_slots=None,
        use_bootstrap=False,
    )
    all_slots = app.runner.child_agents.resolve_slots(
        core,
        input_slots="all",
        output_slots="all",
        use_bootstrap=True,
    )

    assert defaults.to_metadata() == {
        "input_slots": {"serial": ["base_input"], "parallel": []},
        "output_slots": {"serial": ["base_output"], "parallel": []},
        "use_bootstrap": False,
    }
    assert all_slots.to_metadata()["output_slots"] == {"serial": ["base_output", "child_extra"], "parallel": []}
    assert all_slots.use_bootstrap is True


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"input_slots": ["missing"], "output_slots": None, "use_bootstrap": False}, "unknown input slot id: missing"),
        ({"input_slots": "base_input", "output_slots": None, "use_bootstrap": False}, "input_slots must be 'all'"),
        ({"input_slots": ["base_input", "base_input"], "output_slots": None, "use_bootstrap": False}, "duplicate input slot id"),
        ({"input_slots": None, "output_slots": [""], "use_bootstrap": False}, "output slot id must not be empty"),
        ({"input_slots": None, "output_slots": None, "use_bootstrap": "yes"}, "use_bootstrap must be a boolean"),
    ],
)
def test_child_agent_slot_selection_errors(tmp_path, kwargs, message):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    core = app.core_loader.load(app.version_store.active_core_path("evolver"))

    with pytest.raises(ValueError, match=message):
        app.runner.child_agents.resolve_slots(core, **kwargs)


def test_child_agent_tool_selection_modes(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    core = app.core_loader.load(app.version_store.active_core_path("evolver"))

    all_tools = app.runner.child_agents.resolve_tools(core, "all")
    no_tools = app.runner.child_agents.resolve_tools(core, "none")
    selected = app.runner.child_agents.resolve_tools(core, ["read_file"])

    assert all_tools.requested == "all"
    assert "read_file" in all_tools.resolved
    assert no_tools.resolved == []
    assert no_tools.tool_policy == {"allow_exact": []}
    assert selected.requested == ["read_file"]
    assert selected.resolved == ["read_file"]
    assert selected.tool_policy == {"allow_exact": ["read_file"]}


@pytest.mark.parametrize(
    ("requested", "message"),
    [
        ("read_file", "tools must be 'all', 'none', or a list of tool ids"),
        (["read_file", "read_file"], "duplicate tool id"),
        ([""], "tool id must not be empty"),
        ([1], "tools items must be strings"),
        (["missing"], "unknown child tool id: missing"),
    ],
)
def test_child_agent_tool_selection_errors(tmp_path, requested, message):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    core = app.core_loader.load(app.version_store.active_core_path("evolver"))

    with pytest.raises(ValueError, match=message):
        app.runner.child_agents.resolve_tools(core, requested)


def test_delegate_fork_context_uses_parent_turn_session(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    session_a = app.runner.session_id
    app.session_runtime.append_message(
        session_a,
        role="user",
        content="history-A",
        turn_id="turn_a",
    )
    session_b = app.session_runtime.create_session(
        core_id="assistant",
        core_revision="rev_b",
    ).session_id
    app.session_runtime.append_message(
        session_b,
        role="user",
        content="history-B",
        turn_id="turn_b",
    )
    app.runner.session_id = session_b

    context = app.runner.child_agents.delegation_context(
        "fork",
        session_id=session_a,
    )

    assert context == ["Parent session fork context:\nuser: history-A"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("arguments", "metadata", "message"),
    [
        ({}, {}, "goal is required"),
        ({"goal": "child", "tool_policy": {"deny": ["read_file"]}}, {}, "tool_policy is not supported"),
        ({"goal": "child", "core_id": "evolver", "context_mode": "shared"}, {}, "unsupported context_mode: shared"),
        ({"goal": "child", "core_id": "evolver", "notify_policy": "notify"}, {}, "unsupported notify_policy: notify"),
        ({"goal": "child", "core_id": "evolver"}, {"delegation_depth": 2}, "delegation depth limit exceeded: max_depth=2"),
    ],
)
async def test_delegate_task_validation_lives_in_child_agent_runtime(tmp_path, arguments, metadata, message):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))

    result = await app.runner.child_agents.handle_delegate_task(
        ToolCall(name="delegate_task", arguments=arguments),
        core=core,
        turn=_turn(app, core, metadata=metadata),
        capability=CapabilityFacade(core),
    )

    assert result.is_error is True
    assert message in result.content
