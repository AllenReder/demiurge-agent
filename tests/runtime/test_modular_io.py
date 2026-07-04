import asyncio
import shutil

import pytest
import yaml

from demiurge.app import create_app, source_agents_root
from demiurge.providers import LLMResponse, ToolCall
from demiurge.runtime.interactions import InteractionInbound, InteractionRuntime
from demiurge.security.approval import ApprovalDecision
from demiurge.storage import StateStore


class RecordingProvider:
    def __init__(self, responses=None, *, default: str = "main"):
        self.responses = list(responses or [])
        self.default = default
        self.requests = []

    async def complete(self, request):
        self.requests.append(request)
        if self.responses:
            item = self.responses.pop(0)
            delay = 0
            if isinstance(item, tuple):
                item, delay = item
            if delay:
                await asyncio.sleep(delay)
            if isinstance(item, LLMResponse):
                return item
            return LLMResponse(content=str(item))
        return LLMResponse(content=self.default)


class ToolCallingProvider(RecordingProvider):
    async def complete(self, request):
        self.requests.append(request)
        has_tool_result = any(message.role == "tool" for message in request.messages)
        if not has_tool_result:
            return LLMResponse(tool_calls=[ToolCall(id="tool_call_1", name="tools_list", arguments={})])
        return LLMResponse(content="tool done")


class RecordingBridge:
    def __init__(self):
        self.outbounds = []

    async def deliver(self, outbound):
        self.outbounds.append(outbound)
        outbound.mark_delivered()

    async def prompt_user(self, prompt):
        return ""

    async def request_approval(self, request):
        return ApprovalDecision("deny", "test bridge")


class FailingBridge(RecordingBridge):
    async def deliver(self, outbound):
        raise RuntimeError("bridge boom")


def _copy_agents(tmp_path):
    target = tmp_path / "agents"
    shutil.copytree(source_agents_root(), target)
    return target


def _write_module(root, rel_path, code):
    path = root / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(code, encoding="utf-8")


def _write_slot(root, rel_path, text):
    path = root / rel_path
    parts = path.relative_to(root).parts
    if len(parts) == 5 and parts[1] == "agent" and parts[2] == "tools" and parts[4] == "slot.yaml":
        path = path.with_name("tool.yaml")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_pipeline(root, phase, *, serial=None, parallel=None, core_id="assistant"):
    serial = serial or []
    parallel = parallel or []
    pipelines = _load_pipelines_yaml(root, core_id)
    pipelines[phase] = {"serial": list(serial)}
    if phase != "bootstrap":
        pipelines[phase]["parallel"] = list(parallel)
    _write_pipelines_yaml(root, core_id, pipelines)


def _load_pipelines_yaml(root, core_id="assistant"):
    path = root / core_id / "agent" / "pipelines.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else None
    data = raw if isinstance(raw, dict) else {}
    data.setdefault("schema_version", 1)
    for phase in ("bootstrap", "input", "output"):
        data.setdefault(phase, {"serial": []} if phase == "bootstrap" else {"serial": [], "parallel": []})
    return data


def _write_pipelines_yaml(root, core_id, data):
    path = root / core_id / "agent" / "pipelines.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _slot_text(*, description="test slot", failure_policy="soft", capabilities=None):
    capabilities = capabilities or []
    lines = [
        "entrypoint: module:process",
        f"description: {description}",
        f"failure_policy: {failure_policy}",
        "capabilities:",
    ]
    if capabilities:
        lines.extend(f"  - {capability}" for capability in capabilities)
    else:
        lines.append("  []")
    return "\n".join(lines) + "\n"


def _delivery_texts(result) -> list[str]:
    return [delivery.text for delivery in result.deliveries]


def _bridge_deliveries(bridge: RecordingBridge):
    return [delivery for outbound in bridge.outbounds for delivery in outbound.deliveries]


@pytest.mark.asyncio
async def test_input_serial_appends_user_fragments_and_transient_system(tmp_path):
    agents = _copy_agents(tmp_path)
    _write_module(
        agents,
        "assistant/agent/input/prefix/module.py",
        "def process(ctx):\n"
        "    ctx.input.add('system', 'SYS')\n"
        "    ctx.input.add('user', 'FIRST')\n",
    )
    _write_slot(agents, "assistant/agent/input/prefix/slot.yaml", _slot_text())
    _write_module(
        agents,
        "assistant/agent/input/tail/module.py",
        "def process(ctx):\n"
        "    ctx.input.add('user', 'TAIL', history_policy='transient')\n",
    )
    _write_slot(agents, "assistant/agent/input/tail/slot.yaml", _slot_text())
    _write_pipeline(agents, "input", serial=["prefix", "base_input", "tail"])
    _write_pipeline(agents, "output", serial=["base_output"])
    app = create_app(home=tmp_path / "home", provider_name="fake", agents_root=agents)
    provider = RecordingProvider(default="main")
    app.runner.provider = provider

    await app.runner.run_turn("hello")

    request_messages = provider.requests[0].messages
    system_messages = [message for message in request_messages if message.role == "system"]
    assert len(system_messages) == 1
    assert "SYS" in system_messages[0].content
    current_user = [message for message in request_messages if message.role == "user"][-1]
    assert current_user.content == "FIRST\n\nhello\n\nTAIL"
    history = app.session_runtime.read_messages(app.runner.session_id)
    assert [message.role for message in history] == ["user", "assistant"]
    assert history[0].content == "FIRST\n\nhello"
    assert all(message.role != "system" for message in history)


@pytest.mark.asyncio
async def test_system_prompt_debug_disabled_by_default(tmp_path):
    agents = _copy_agents(tmp_path)
    _write_pipeline(agents, "output", serial=["base_output"])
    app = create_app(home=tmp_path / "home", provider_name="fake", agents_root=agents)
    app.runner.provider = RecordingProvider(default="main")
    bridge = RecordingBridge()
    runtime = InteractionRuntime(app.runner)

    await runtime.handle(InteractionInbound(channel="tui", text="hello", source="local"), bridge=bridge)
    await app.runner.drain_background_tasks()

    deliveries = _bridge_deliveries(bridge)
    assert all(delivery.metadata.get("debug") != "system_prompt" for delivery in deliveries)


@pytest.mark.asyncio
async def test_system_prompt_debug_delivers_transient_actual_system_context(tmp_path):
    agents = _copy_agents(tmp_path)
    _write_module(
        agents,
        "assistant/agent/bootstrap/boot/module.py",
        "def process(ctx):\n"
        "    ctx.bootstrap.add('BOOT SYSTEM')\n",
    )
    _write_slot(agents, "assistant/agent/bootstrap/boot/slot.yaml", _slot_text())
    _write_pipeline(agents, "bootstrap", serial=["boot"])
    _write_module(
        agents,
        "assistant/agent/input/debug_system/module.py",
        "def process(ctx):\n"
        "    ctx.input.add('system', 'TURN SYSTEM')\n",
    )
    _write_slot(agents, "assistant/agent/input/debug_system/slot.yaml", _slot_text())
    _write_pipeline(agents, "input", serial=["debug_system", "base_input"])
    _write_pipeline(agents, "output", serial=["base_output"])
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.yaml").write_text("debug:\n  show_system_prompt: true\n", encoding="utf-8")
    app = create_app(home=home, provider_name="fake", agents_root=agents)
    app.runner.provider = RecordingProvider(default="main")
    bridge = RecordingBridge()
    runtime = InteractionRuntime(app.runner)

    await runtime.handle(InteractionInbound(channel="tui", text="hello", source="local"), bridge=bridge)
    await app.runner.drain_background_tasks()

    deliveries = _bridge_deliveries(bridge)
    debug_delivery = next(delivery for delivery in deliveries if delivery.metadata.get("debug") == "system_prompt")
    assert deliveries.index(debug_delivery) == 0
    assert debug_delivery.kind == "notice"
    assert debug_delivery.history_policy == "transient"
    assert debug_delivery.metadata["role"] == "system"
    assert "# System prompt debug" in debug_delivery.text
    assert "## Final system prompt" in debug_delivery.text
    assert "## System message" not in debug_delivery.text
    assert "BOOT SYSTEM" in debug_delivery.text
    assert "TURN SYSTEM" in debug_delivery.text
    assert "hello" not in debug_delivery.text
    assert "main" not in debug_delivery.text
    messages = app.session_runtime.read_messages(app.runner.session_id)
    assert all("BOOT SYSTEM" not in message.content for message in messages)
    assert all("TURN SYSTEM" not in message.content for message in messages)
    assert all(message.role != "system" for message in messages)


@pytest.mark.asyncio
async def test_system_prompt_debug_delivers_once_per_model_step(tmp_path):
    agents = _copy_agents(tmp_path)
    _write_pipeline(agents, "output", serial=["base_output"])
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.yaml").write_text("debug:\n  show_system_prompt: true\n", encoding="utf-8")
    app = create_app(home=home, provider_name="fake", agents_root=agents)
    app.runner.provider = ToolCallingProvider()
    bridge = RecordingBridge()
    runtime = InteractionRuntime(app.runner)

    await runtime.handle(InteractionInbound(channel="tui", text="tools_list", source="local"), bridge=bridge)
    await app.runner.drain_background_tasks()

    debug_deliveries = [delivery for delivery in _bridge_deliveries(bridge) if delivery.metadata.get("debug") == "system_prompt"]
    assert len(debug_deliveries) == 2
    assert f"step: {app.runner.display_turns[-1]['turn_id']}_step_1" in debug_deliveries[0].text
    assert f"step: {app.runner.display_turns[-1]['turn_id']}_step_2" in debug_deliveries[1].text


@pytest.mark.asyncio
async def test_input_module_can_send_delivery(tmp_path):
    agents = _copy_agents(tmp_path)
    _write_module(
        agents,
        "assistant/agent/input/notice_input/module.py",
        "def process(ctx):\n"
        "    ctx.input.add('user', ctx.input.raw_input.text)\n"
        "    ctx.input.send_text('input note', history_policy='model_hidden')\n",
    )
    _write_slot(agents, "assistant/agent/input/notice_input/slot.yaml", _slot_text())
    _write_pipeline(agents, "input", serial=["notice_input"])
    _write_pipeline(agents, "output", serial=["base_output"])
    app = create_app(home=tmp_path / "home", provider_name="fake", agents_root=agents)
    app.runner.provider = RecordingProvider(default="main")

    result = await app.runner.run_turn("hello")

    assert _delivery_texts(result) == ["input note", "main"]
    messages = app.session_runtime.read_messages(app.runner.session_id)
    input_message = next(message for message in messages if message.content == "input note")
    assert input_message.role == "assistant"
    assert input_message.model_visible is False
    assert input_message.metadata["phase"] == "input"


@pytest.mark.asyncio
async def test_input_pipeline_without_user_fragment_fails_runtime(tmp_path):
    agents = _copy_agents(tmp_path)
    _write_module(agents, "assistant/agent/input/no_user/module.py", "def process(ctx):\n    pass\n")
    _write_slot(agents, "assistant/agent/input/no_user/slot.yaml", _slot_text(failure_policy="hard"))
    _write_pipeline(agents, "input", serial=["no_user"])
    app = create_app(home=tmp_path / "home", provider_name="fake", agents_root=agents)

    with pytest.raises(RuntimeError, match="input pipeline did not produce a user message"):
        await app.runner.run_turn("hello")


@pytest.mark.asyncio
async def test_parallel_input_cannot_modify_current_prompt(tmp_path):
    agents = _copy_agents(tmp_path)
    _write_module(
        agents,
        "assistant/agent/input/bad_parallel/module.py",
        "def process(ctx):\n"
        "    ctx.input.add('user', 'BAD')\n",
    )
    _write_slot(agents, "assistant/agent/input/bad_parallel/slot.yaml", _slot_text())
    _write_pipeline(agents, "input", serial=["base_input"], parallel=["bad_parallel"])
    _write_pipeline(agents, "output", serial=["base_output"])
    app = create_app(home=tmp_path / "home", provider_name="fake", agents_root=agents)
    provider = RecordingProvider(default="main")
    app.runner.provider = provider

    await app.runner.run_turn("hello")
    await app.runner.drain_background_tasks()

    request_text = "\n".join(message.content for message in provider.requests[0].messages)
    assert "hello" in request_text
    assert "BAD" not in request_text
    assert any(
        event["type"] == "module.failed"
        and event["slot"] == "agent/input/bad_parallel"
        and "parallel input modules cannot modify" in event["error"]
        for event in app.runner.event_log.tail(30)
    )


@pytest.mark.asyncio
async def test_output_pipeline_without_send_does_not_deliver_or_persist_assistant(tmp_path):
    agents = _copy_agents(tmp_path)
    _write_pipeline(agents, "output", serial=[])
    app = create_app(home=tmp_path / "home", provider_name="fake", agents_root=agents)
    app.runner.provider = RecordingProvider(default="hidden")

    result = await app.runner.run_turn("hello")

    assert result.deliveries == []
    messages = app.session_runtime.read_messages(app.runner.session_id)
    assert [(message.role, message.content) for message in messages] == [("user", "hello")]


@pytest.mark.asyncio
async def test_output_explicit_delivery_and_history_policy(tmp_path):
    agents = _copy_agents(tmp_path)
    _write_module(
        agents,
        "assistant/agent/output/extra/module.py",
        "def process(ctx):\n"
        "    ctx.output.send_text('extra', history_policy='model_hidden')\n",
    )
    _write_slot(agents, "assistant/agent/output/extra/slot.yaml", _slot_text())
    _write_pipeline(agents, "output", serial=["base_output", "extra"])
    app = create_app(home=tmp_path / "home", provider_name="fake", agents_root=agents)
    app.runner.provider = RecordingProvider(default="main")

    result = await app.runner.run_turn("hello")

    assert _delivery_texts(result) == ["main", "extra"]
    assistant_history = [
        (message.content, message.model_visible)
        for message in app.session_runtime.read_messages(app.runner.session_id)
        if message.role == "assistant"
    ]
    assert assistant_history == [("main", True), ("extra", False)]
    assert result.deliveries[0].metadata["phase"] == "output"


@pytest.mark.asyncio
async def test_immediate_delivery_commits_history_and_avoids_final_outbound_duplicate(tmp_path):
    agents = _copy_agents(tmp_path)
    _write_module(
        agents,
        "assistant/agent/output/immediate/module.py",
        "def process(ctx):\n"
        "    ctx.output.send_text('persist immediate')\n"
        "    ctx.output.send_text('hidden immediate', visible=False, write_history=True)\n"
        "    recent = [(m.content, m.model_visible) for m in ctx.history.recent_messages(5)]\n"
        "    ctx.result.set({'recent': recent})\n",
    )
    _write_slot(agents, "assistant/agent/output/immediate/slot.yaml", _slot_text())
    _write_pipeline(agents, "output", serial=["immediate"])
    app = create_app(home=tmp_path / "home", provider_name="fake", agents_root=agents)
    app.runner.provider = RecordingProvider(default="main")
    bridge = RecordingBridge()
    runtime = InteractionRuntime(app.runner)

    outbound = await runtime.handle(
        InteractionInbound(channel="telegram", text="hello", source="123", reply_to="456", conversation_key="telegram:123"),
        bridge=bridge,
    )
    await app.runner.drain_background_tasks()

    assert outbound.deliveries == []
    assert [delivery.text for outbound in bridge.outbounds for delivery in outbound.deliveries] == [
        "persist immediate",
    ]
    messages = app.session_runtime.read_messages(app.runner.session_id)
    assert [(message.role, message.content, message.visible, message.model_visible) for message in messages] == [
        ("user", "hello", True, True),
        ("assistant", "persist immediate", True, True),
        ("assistant", "hidden immediate", False, True),
    ]
    completed = next(event for event in app.runner.event_log.tail(30) if event["type"] == "turn.completed")
    assert ("persist immediate", True) in {
        tuple(item) for item in completed["agent_result"]["recent"]
    }
    assert ("hidden immediate", True) in {
        tuple(item) for item in completed["agent_result"]["recent"]
    }


@pytest.mark.asyncio
async def test_output_context_does_not_expose_ctx_io(tmp_path):
    agents = _copy_agents(tmp_path)
    _write_module(
        agents,
        "assistant/agent/output/probe/module.py",
        "def process(ctx):\n"
        "    ctx.result.set({'has_io': hasattr(ctx, 'io')})\n"
        "    ctx.output.send_text(ctx.output.content)\n",
    )
    _write_slot(agents, "assistant/agent/output/probe/slot.yaml", _slot_text())
    _write_pipeline(agents, "output", serial=["probe"])
    app = create_app(home=tmp_path / "home", provider_name="fake", agents_root=agents)
    app.runner.provider = RecordingProvider(default="main")

    result = await app.runner.run_turn("hello")

    assert result.agent_result == {"has_io": False}


@pytest.mark.asyncio
async def test_module_contexts_do_not_expose_path_attachment_api(tmp_path):
    removed_api = "attach" + "_path"
    agents = _copy_agents(tmp_path)
    _write_module(
        agents,
        "assistant/agent/input/probe/module.py",
        "def process(ctx):\n"
        f"    if hasattr(ctx.input, {removed_api!r}):\n"
        "        raise RuntimeError('ctx.input path attachment API should not exist')\n"
        "    ctx.input.add_context(ctx.input.raw_text, role='user')\n"
        "    ctx.input.send_text('input probe', write_history=False)\n",
    )
    _write_module(
        agents,
        "assistant/agent/output/output_probe/module.py",
        "def process(ctx):\n"
        "    ctx.result.set({\n"
        f"        'output_has_attach': hasattr(ctx.output, {removed_api!r}),\n"
        f"        'result_has_attach': hasattr(ctx.result, {removed_api!r}),\n"
        "    })\n"
        "    ctx.output.send_text(ctx.output.response_text)\n",
    )
    _write_slot(agents, "assistant/agent/input/probe/slot.yaml", _slot_text())
    _write_slot(agents, "assistant/agent/output/output_probe/slot.yaml", _slot_text())
    _write_pipeline(agents, "input", serial=["probe"])
    _write_pipeline(agents, "output", serial=["output_probe"])
    app = create_app(home=tmp_path / "home", provider_name="fake", agents_root=agents)
    app.runner.provider = RecordingProvider(default="main")

    result = await app.runner.run_turn("hello")

    assert result.agent_result == {
        "output_has_attach": False,
        "result_has_attach": False,
    }


@pytest.mark.asyncio
async def test_tool_step_transcript_persists_and_output_history_sees_current_turn(tmp_path):
    agents = _copy_agents(tmp_path)
    _write_module(
        agents,
        "assistant/agent/output/probe/module.py",
        "def process(ctx):\n"
        "    items = []\n"
        "    for message in ctx.history.recent_messages(10):\n"
        "        items.append({\n"
        "            'message_id': message.message_id,\n"
        "            'role': message.role,\n"
        "            'content': message.content,\n"
        "            'turn_id': message.turn_id,\n"
        "            'step_id': message.step_id,\n"
        "            'tool_call_id': message.tool_call_id,\n"
        "            'tool_calls': list(message.tool_calls),\n"
        "            'visible': message.visible,\n"
        "            'model_visible': message.model_visible,\n"
        "            'tool_name': message.tool_name,\n"
        "            'is_error': message.is_error,\n"
        "        })\n"
        "    ctx.result.set({'history': items})\n"
        "    ctx.output.send_text(ctx.output.content, history_policy='persist')\n",
    )
    _write_slot(agents, "assistant/agent/output/probe/slot.yaml", _slot_text())
    _write_pipeline(agents, "output", serial=["probe"])
    app = create_app(home=tmp_path / "home", provider_name="fake", agents_root=agents)
    provider = RecordingProvider(
        responses=[
            LLMResponse(content="checking tools", tool_calls=[ToolCall(id="tools_1", name="tools_list", arguments={})]),
            LLMResponse(content="done"),
        ]
    )
    app.runner.provider = provider

    result = await app.runner.run_turn("inspect tools")

    assert _delivery_texts(result) == ["checking tools", "done"]
    messages = app.session_runtime.read_messages(app.runner.session_id)
    assert [(message.role, message.content, message.visible, message.model_visible) for message in messages] == [
        ("user", "inspect tools", True, True),
        ("assistant", "checking tools", True, True),
        ("tool", messages[2].content, False, True),
        ("assistant", "done", True, True),
    ]
    assert messages[1].metadata["step_id"].endswith("_step_1")
    assert messages[1].metadata["tool_calls"] == [{"name": "tools_list", "arguments": {}, "id": "tools_1"}]
    assert messages[2].metadata["step_id"] == messages[1].metadata["step_id"]
    assert messages[2].metadata["tool_name"] == "tools_list"
    assert messages[2].metadata["tool_call_id"] == "tools_1"
    assert [message.content for message in messages if message.content == "done"] == ["done"]

    second_request_roles = [message.role for message in provider.requests[1].messages]
    assert second_request_roles[-3:] == ["user", "assistant", "tool"]
    assert provider.requests[1].messages[-2].tool_calls[0].name == "tools_list"
    assert provider.requests[1].messages[-1].tool_call_id == "tools_1"

    history = result.agent_result["history"]
    assert [item["role"] for item in history] == ["user", "assistant", "tool"]
    assert history[1]["tool_calls"][0]["name"] == "tools_list"
    assert history[2]["tool_name"] == "tools_list"
    assert history[2]["visible"] is False
    assert history[2]["model_visible"] is True

    provider.responses.append(LLMResponse(content="next done"))
    await app.runner.run_turn("next turn")
    next_turn_request = provider.requests[2]
    assert any(message.role == "assistant" and message.tool_calls for message in next_turn_request.messages)
    assert any(
        message.role == "tool" and message.tool_call_id == "tools_1" and message.name == "tools_list"
        for message in next_turn_request.messages
    )


@pytest.mark.asyncio
async def test_authored_tool_can_send_transient_audio_without_history(tmp_path):
    agents = _copy_agents(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "voice.mp3").write_bytes(b"AUDIO")
    _write_module(
        agents,
        "assistant/agent/tools/voice_sender/module.py",
        "from demiurge.sdk import ToolResult\n"
        "def execute(ctx, args):\n"
        "    ctx.output.send_audio('voice.mp3', media_type='audio/mpeg', history_policy='transient')\n"
        "    return ToolResult(content='sent audio')\n",
    )
    _write_slot(
        agents,
        "assistant/agent/tools/voice_sender/slot.yaml",
        "entrypoint: module:execute\n"
        "description: Send test audio.\n"
        "input_schema:\n"
        "  type: object\n"
        "  properties: {}\n"
        "  additionalProperties: false\n"
        "capabilities: []\n",
    )
    _write_pipeline(agents, "output", serial=["base_output"])
    app = create_app(home=tmp_path / "home", provider_name="fake", agents_root=agents, workspace=workspace)
    app.runner.provider = RecordingProvider(
        responses=[
            LLMResponse(tool_calls=[ToolCall(id="voice_1", name="voice_sender", arguments={})]),
            LLMResponse(content="done"),
        ]
    )

    result = await app.runner.run_turn("make voice")

    audio_delivery = next(
        delivery for delivery in result.deliveries if any(block.get("type") == "audio" for block in delivery.blocks)
    )
    assert audio_delivery.history_policy == "transient"
    assert audio_delivery.metadata["slot"] == "agent/tools/voice_sender"
    assert audio_delivery.metadata["phase"] == "tool"
    messages = app.session_runtime.read_messages(app.runner.session_id)
    assert [(message.role, message.content, message.model_visible) for message in messages] == [
        ("user", "make voice", True),
        ("assistant", "", True),
        ("tool", "sent audio", True),
        ("assistant", "done", True),
    ]
    assert not any(message.role == "assistant" and "artifact:" in message.content for message in messages)


@pytest.mark.asyncio
async def test_tool_output_defaults_to_persist_when_write_history_is_omitted(tmp_path):
    agents = _copy_agents(tmp_path)
    _write_module(
        agents,
        "assistant/agent/tools/default_sender/module.py",
        "from demiurge.sdk import ToolResult\n"
        "def execute(ctx, args):\n"
        "    ctx.output.send_text('default policy')\n"
        "    return ToolResult(content='default ok')\n",
    )
    _write_slot(
        agents,
        "assistant/agent/tools/default_sender/slot.yaml",
        "entrypoint: module:execute\n"
            "description: Send text with default history write behavior.\n"
        "input_schema:\n"
        "  type: object\n"
        "  properties: {}\n"
        "  additionalProperties: false\n"
        "capabilities: []\n",
    )
    _write_pipeline(agents, "output", serial=[])
    app = create_app(home=tmp_path / "home", provider_name="fake", agents_root=agents)
    app.runner.provider = RecordingProvider(
        responses=[
            LLMResponse(tool_calls=[ToolCall(id="default_1", name="default_sender", arguments={})]),
            LLMResponse(content=""),
        ]
    )

    result = await app.runner.run_turn("default policy")

    delivery = next(delivery for delivery in result.deliveries if delivery.text == "default policy")
    assert delivery.history_policy == "persist"
    messages = app.session_runtime.read_messages(app.runner.session_id)
    assistant_delivery = next(
        message for message in messages if message.role == "assistant" and message.content == "default policy"
    )
    assert assistant_delivery.visible is True
    assert assistant_delivery.model_visible is True
    tool_message = next(message for message in messages if message.role == "tool")
    assert tool_message.content == "default ok"
    assert tool_message.model_visible is True


@pytest.mark.asyncio
async def test_tool_delivery_schedules_immediately_after_tool_returns(tmp_path):
    agents = _copy_agents(tmp_path)
    _write_module(
        agents,
        "assistant/agent/tools/slot_end_sender/module.py",
        "from demiurge.sdk import ToolResult\n"
        "def execute(ctx, args):\n"
            "    ctx.output.send_text('tool immediate delivery', write_history=False)\n"
        "    return ToolResult(content='slot end ok')\n",
    )
    _write_slot(
        agents,
        "assistant/agent/tools/slot_end_sender/slot.yaml",
        "entrypoint: module:execute\n"
        "description: Send text at tool slot end.\n"
        "input_schema:\n"
        "  type: object\n"
        "  properties: {}\n"
        "  additionalProperties: false\n"
        "capabilities: []\n",
    )
    _write_pipeline(agents, "output", serial=[])
    app = create_app(home=tmp_path / "home", provider_name="fake", agents_root=agents)
    app.runner.provider = RecordingProvider(
        responses=[
            LLMResponse(tool_calls=[ToolCall(id="slot_end_1", name="slot_end_sender", arguments={})]),
            LLMResponse(content=""),
        ]
    )
    bridge = RecordingBridge()

    await InteractionRuntime(app.runner).handle(
        InteractionInbound(channel="tui", text="slot end", source="local", conversation_key="local:slot-end"),
        bridge=bridge,
    )
    await app.runner.drain_background_tasks()

    delivered = [
        delivery.text
        for outbound in bridge.outbounds
        for delivery in outbound.deliveries
    ]
    assert "tool immediate delivery" in delivered


@pytest.mark.asyncio
async def test_module_tool_call_collects_tool_deliveries_in_current_turn(tmp_path):
    agents = _copy_agents(tmp_path)
    _write_module(
        agents,
        "assistant/agent/tools/nested_sender/module.py",
        "from demiurge.sdk import ToolResult\n"
        "def execute(ctx, args):\n"
        "    ctx.output.send_text('nested delivery', history_policy='transient')\n"
        "    return ToolResult(content='nested result')\n",
    )
    _write_slot(
        agents,
        "assistant/agent/tools/nested_sender/slot.yaml",
        "entrypoint: module:execute\n"
        "description: Send nested text.\n"
        "input_schema:\n"
        "  type: object\n"
        "  properties: {}\n"
        "  additionalProperties: false\n"
        "capabilities: []\n",
    )
    _write_module(
        agents,
        "assistant/agent/output/tool_probe/module.py",
        "async def process(ctx):\n"
        "    result = await ctx.tools.call('nested_sender')\n"
        "    ctx.output.send_text('parent saw ' + result.content, history_policy='model_hidden')\n",
    )
    _write_slot(
        agents,
        "assistant/agent/output/tool_probe/slot.yaml",
        _slot_text(capabilities=["tool.call:nested_sender"]),
    )
    _write_pipeline(agents, "output", serial=["tool_probe"])
    app = create_app(home=tmp_path / "home", provider_name="fake", agents_root=agents)
    app.runner.provider = RecordingProvider(default="parent base")

    result = await app.runner.run_turn("hello")

    assert _delivery_texts(result) == ["nested delivery", "parent saw nested result"]
    assert result.deliveries[0].metadata["slot"] == "agent/tools/nested_sender"
    assert result.deliveries[0].metadata["phase"] == "tool"
    assert result.deliveries[1].metadata["slot"] == "agent/output/tool_probe"
    assert result.deliveries[1].metadata["phase"] == "output"


@pytest.mark.asyncio
async def test_runtime_max_model_steps_limits_tool_loop(tmp_path):
    agents = _copy_agents(tmp_path)
    manifest_path = agents / "assistant" / "agent.yaml"
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    raw["runtime"]["max_model_steps"] = 2
    manifest_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    app = create_app(home=tmp_path / "home", provider_name="fake", agents_root=agents)
    app.runner.provider = RecordingProvider(
        responses=[
            LLMResponse(tool_calls=[ToolCall(id="tools_1", name="tools_list", arguments={})]),
            LLMResponse(tool_calls=[ToolCall(id="tools_2", name="tools_list", arguments={})]),
            LLMResponse(content="should not be requested"),
        ]
    )

    result = await app.runner.run_turn("loop")

    assert len(app.runner.provider.requests) == 2
    assert _delivery_texts(result) == [
        "The provider did not produce a final assistant message within the configured step budget of 2."
    ]
    messages = app.session_runtime.read_messages(app.runner.session_id)
    assert [message.role for message in messages] == ["user", "assistant", "tool", "assistant", "tool", "assistant"]
    assert [message.metadata.get("step_id") for message in messages if message.role == "assistant" and message.metadata.get("tool_calls")] == [
        f"{result.turn_id}_step_1",
        f"{result.turn_id}_step_2",
    ]


@pytest.mark.asyncio
async def test_io_delivery_does_not_require_deliver_capability(tmp_path):
    agents = _copy_agents(tmp_path)
    _write_module(
        agents,
        "assistant/agent/output/denied/module.py",
        "def process(ctx):\n"
        "    ctx.output.send_text('denied')\n",
    )
    _write_slot(agents, "assistant/agent/output/denied/slot.yaml", _slot_text())
    _write_pipeline(agents, "output", serial=["base_output", "denied"])
    app = create_app(home=tmp_path / "home", provider_name="fake", agents_root=agents)
    app.runner.provider = RecordingProvider(default="main")

    result = await app.runner.run_turn("hello")

    assert _delivery_texts(result) == ["main", "denied"]
    messages = app.session_runtime.read_messages(app.runner.session_id)
    assert [message.content for message in messages if message.role == "assistant"] == ["main", "denied"]
    assert not any(event["type"] == "capability.denied" for event in app.runner.event_log.tail(30))


@pytest.mark.asyncio
async def test_parallel_output_runs_in_background_and_uses_active_bridge(tmp_path):
    agents = _copy_agents(tmp_path)
    _write_module(
        agents,
        "assistant/agent/output/async_extra/module.py",
        "import asyncio\n"
        "async def process(ctx):\n"
        "    await asyncio.sleep(0.01)\n"
        "    ctx.output.send_text('async extra')\n",
    )
    _write_slot(agents, "assistant/agent/output/async_extra/slot.yaml", _slot_text())
    _write_pipeline(agents, "output", serial=["base_output"], parallel=["async_extra"])
    app = create_app(home=tmp_path / "home", provider_name="fake", agents_root=agents)
    app.runner.provider = RecordingProvider(default="main")
    bridge = RecordingBridge()
    runtime = InteractionRuntime(app.runner)

    outbound = await runtime.handle(
        InteractionInbound(channel="telegram", text="hello", source="123", reply_to="456", conversation_key="telegram:123"),
        bridge=bridge,
    )
    outbound.mark_delivered()
    await app.runner.drain_background_tasks()

    assert outbound.deliveries == []
    assert [delivery.text for outbound in bridge.outbounds for delivery in outbound.deliveries] == ["main", "async extra"]
    assert bridge.outbounds[-1].metadata["source"] == "123"
    assert bridge.outbounds[-1].metadata["reply_to"] == "456"
    assert bridge.outbounds[-1].metadata["slot"] == "agent/output/async_extra"
    assert bridge.outbounds[-1].metadata["phase"] == "output"
    assert bridge.outbounds[-1].metadata["session_id"] == app.runner.session_id
    assert bridge.outbounds[-1].metadata["turn_id"] == outbound.turn_id


@pytest.mark.asyncio
async def test_progress_flushes_immediately_without_persisting_history(tmp_path):
    agents = _copy_agents(tmp_path)
    _write_module(
        agents,
        "assistant/agent/output/immediate_progress/module.py",
        "def process(ctx):\n"
        "    ctx.output.progress('working')\n"
        "    ctx.output.send_text('done', history_policy='model_hidden')\n",
    )
    _write_slot(agents, "assistant/agent/output/immediate_progress/slot.yaml", _slot_text())
    _write_pipeline(agents, "output", serial=["base_output", "immediate_progress"])
    app = create_app(home=tmp_path / "home", provider_name="fake", agents_root=agents)
    app.runner.provider = RecordingProvider(default="main")
    bridge = RecordingBridge()
    runtime = InteractionRuntime(app.runner)

    outbound = await runtime.handle(
        InteractionInbound(channel="telegram", text="hello", source="123", reply_to="456", conversation_key="telegram:123"),
        bridge=bridge,
    )
    outbound.mark_delivered()
    await app.runner.drain_background_tasks()

    assert outbound.deliveries == []
    delivered = [(delivery.kind, delivery.text) for outbound in bridge.outbounds for delivery in outbound.deliveries]
    assert delivered == [("message", "main"), ("progress", "working"), ("message", "done")]
    messages = app.session_runtime.read_messages(app.runner.session_id)
    assert [(message.role, message.content) for message in messages] == [
        ("user", "hello"),
        ("assistant", "main"),
        ("assistant", "done"),
    ]


@pytest.mark.asyncio
async def test_immediate_delivery_failure_is_nonfatal_and_keeps_history(tmp_path):
    agents = _copy_agents(tmp_path)
    _write_pipeline(agents, "output", serial=["base_output"])
    app = create_app(home=tmp_path / "home", provider_name="fake", agents_root=agents)
    app.runner.provider = RecordingProvider(default="main")
    runtime = InteractionRuntime(app.runner)

    outbound = await runtime.handle(
        InteractionInbound(channel="telegram", text="hello", source="123", reply_to="456", conversation_key="telegram:123"),
        bridge=FailingBridge(),
    )
    await app.runner.drain_background_tasks()

    assert outbound.deliveries == []
    messages = app.session_runtime.read_messages(app.runner.session_id)
    assert [(message.role, message.content, message.model_visible) for message in messages] == [
        ("user", "hello", True),
        ("assistant", "main", True),
    ]
    assert any(event["type"] == "turn.completed" for event in app.runner.event_log.tail(30))
    assert any(
        event["type"] == "delivery.failed"
        and event.get("reason") == "bridge_deliver_failed"
        and event.get("error") == "bridge boom"
        and event.get("slot") == "agent/output/base_output"
        for event in app.runner.event_log.tail(30)
    )


@pytest.mark.asyncio
async def test_output_send_commits_history_and_schedules_delivery_immediately(tmp_path):
    agents = _copy_agents(tmp_path)
    _write_module(
        agents,
        "assistant/agent/output/slot_end/module.py",
        "def process(ctx):\n"
        "    ctx.output.send_text('slot-end')\n"
        "    recent = [m.content for m in ctx.history.recent_messages(10)]\n"
        "    ctx.result.set({'saw_slot_end': 'slot-end' in recent})\n",
    )
    _write_slot(agents, "assistant/agent/output/slot_end/slot.yaml", _slot_text())
    _write_pipeline(agents, "output", serial=["base_output", "slot_end"])
    app = create_app(home=tmp_path / "home", provider_name="fake", agents_root=agents)
    app.runner.provider = RecordingProvider(default="main")
    bridge = RecordingBridge()
    runtime = InteractionRuntime(app.runner)

    outbound = await runtime.handle(
        InteractionInbound(channel="telegram", text="hello", source="123", reply_to="456", conversation_key="telegram:123"),
        bridge=bridge,
    )
    outbound.mark_delivered()
    await app.runner.drain_background_tasks()

    assert outbound.deliveries == []
    assert [delivery.text for outbound in bridge.outbounds for delivery in outbound.deliveries] == ["main", "slot-end"]
    assert app.runner.display_turns[-1]["assistant"] == ["main", "slot-end"]
    assert app.session_runtime.read_messages(app.runner.session_id)[-1].content == "slot-end"
    assert any(
        event["type"] == "turn.completed" and event.get("agent_result") == {"saw_slot_end": True}
        for event in app.runner.event_log.tail(30)
    )


@pytest.mark.asyncio
async def test_parallel_output_without_bridge_writes_delivery_failed_event(tmp_path):
    agents = _copy_agents(tmp_path)
    _write_module(
        agents,
        "assistant/agent/output/async_extra/module.py",
        "import asyncio\n"
        "async def process(ctx):\n"
        "    await asyncio.sleep(0.01)\n"
        "    ctx.output.send_text('async extra')\n",
    )
    _write_slot(agents, "assistant/agent/output/async_extra/slot.yaml", _slot_text())
    _write_pipeline(agents, "output", serial=["base_output"], parallel=["async_extra"])
    app = create_app(home=tmp_path / "home", provider_name="fake", agents_root=agents)
    app.runner.provider = RecordingProvider(default="main")
    runtime = InteractionRuntime(app.runner)

    outbound = await runtime.handle(
        InteractionInbound(channel="telegram", text="hello", source="123", reply_to="456", conversation_key="telegram:123"),
        bridge=None,
    )
    await app.runner.drain_background_tasks()

    assert [delivery.text for delivery in outbound.deliveries] == ["main"]
    assert any(
        event["type"] == "delivery.failed"
        and event.get("reason") == "no_active_interaction_bridge"
        and event.get("channel") == "telegram"
        and event.get("slot") == "agent/output/async_extra"
        and event.get("delivery_id")
        and event.get("history_policy") == "transient"
        for event in app.runner.event_log.tail(30)
    )


@pytest.mark.asyncio
async def test_parallel_output_bridge_failure_writes_delivery_failed_event(tmp_path):
    agents = _copy_agents(tmp_path)
    _write_module(
        agents,
        "assistant/agent/output/async_extra/module.py",
        "import asyncio\n"
        "async def process(ctx):\n"
        "    await asyncio.sleep(0.01)\n"
        "    ctx.output.send_text('async extra')\n",
    )
    _write_slot(agents, "assistant/agent/output/async_extra/slot.yaml", _slot_text())
    _write_pipeline(agents, "output", serial=["base_output"], parallel=["async_extra"])
    app = create_app(home=tmp_path / "home", provider_name="fake", agents_root=agents)
    app.runner.provider = RecordingProvider(default="main")
    runtime = InteractionRuntime(app.runner)

    outbound = await runtime.handle(
        InteractionInbound(channel="telegram", text="hello", source="123", reply_to="456", conversation_key="telegram:123"),
        bridge=FailingBridge(),
    )
    outbound.mark_delivered()
    await app.runner.drain_background_tasks()

    assert outbound.deliveries == []
    assert any(
        event["type"] == "delivery.failed"
        and event.get("reason") == "bridge_deliver_failed"
        and event.get("error") == "bridge boom"
        and event.get("slot") == "agent/output/async_extra"
        and event.get("delivery_id")
        for event in app.runner.event_log.tail(30)
    )


@pytest.mark.asyncio
async def test_scoped_module_state_client_writes_runtime_state(tmp_path):
    agents = _copy_agents(tmp_path)
    _write_module(
        agents,
        "assistant/agent/output/mood/module.py",
        "def process(ctx):\n"
        "    ctx.state.set('module_state.mood', 'happy')\n",
    )
    _write_slot(agents, "assistant/agent/output/mood/slot.yaml", _slot_text(capabilities=["state.write:module_state.mood"]))
    _write_pipeline(agents, "output", serial=["base_output", "mood"])
    app = create_app(home=tmp_path / "home", provider_name="fake", agents_root=agents)
    app.runner.provider = RecordingProvider(default="main")

    await app.runner.run_turn("hello")

    assert StateStore(app.home, "assistant").read()["module_state"]["mood"] == "happy"


@pytest.mark.asyncio
async def test_history_recent_messages_returns_message_order_with_tool_summary(tmp_path):
    agents = _copy_agents(tmp_path)
    _write_module(
        agents,
        "assistant/agent/output/history_probe/module.py",
        "def process(ctx):\n"
        "    rows = []\n"
        "    for item in ctx.history.recent_messages(4):\n"
        "        rows.append(f'{item.role}:{item.tool_name}:{item.content}')\n"
        "    ctx.output.send_text('|'.join(rows), history_policy='model_hidden')\n",
    )
    _write_slot(agents, "assistant/agent/output/history_probe/slot.yaml", _slot_text())
    _write_pipeline(agents, "output", serial=["base_output", "history_probe"])
    app = create_app(home=tmp_path / "home", provider_name="fake", agents_root=agents)
    app.runner.provider = ToolCallingProvider()

    result = await app.runner.run_turn("tools_list")

    assert _delivery_texts(result)[0] == "tool done"
    summary = _delivery_texts(result)[1]
    assert "user:None:tools_list" in summary
    assert "assistant:None:" in summary
    assert "tool:tools_list:" in summary
    assert "assistant:None:tool done" in summary
    messages = app.session_runtime.read_messages(app.runner.session_id)
    assert [message.role for message in messages[:4]] == ["user", "assistant", "tool", "assistant"]
    assert messages[1].metadata["tool_calls"][0]["name"] == "tools_list"
    assert messages[2].model_visible is True


@pytest.mark.asyncio
async def test_tool_result_dispatches_before_slow_output_delivery(tmp_path):
    agents = _copy_agents(tmp_path)
    _write_module(
        agents,
        "assistant/agent/output/slow_audio/module.py",
        "import asyncio\n"
        "async def process(ctx):\n"
        "    await asyncio.sleep(0.02)\n"
        "    ctx.output.send_text('slow artifact')\n",
    )
    _write_slot(agents, "assistant/agent/output/slow_audio/slot.yaml", _slot_text())
    _write_pipeline(agents, "output", serial=["base_output", "slow_audio"])
    app = create_app(home=tmp_path / "home", provider_name="fake", agents_root=agents)
    app.runner.provider = ToolCallingProvider()
    bridge = RecordingBridge()
    runtime = InteractionRuntime(app.runner)

    outbound = await runtime.handle(
        InteractionInbound(channel="tui", text="tools_list", source="local", conversation_key="local:test"),
        bridge=bridge,
    )
    await app.runner.drain_background_tasks()

    assert outbound.items == []
    ordered = [
        (
            item.kind,
            item.tool_call.status
            if item.tool_call is not None
            else item.tool_result.call.name
            if item.tool_result is not None
            else item.delivery.text,
        )
        for outbound in bridge.outbounds
        for item in outbound.items
    ]
    assert ordered == [
        ("tool_call", "running"),
        ("tool_call", "ok"),
        ("delivery", "tool done"),
        ("delivery", "slow artifact"),
    ]


@pytest.mark.asyncio
async def test_all_requested_tool_calls_start_before_execution_finishes(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    app.runner.provider = RecordingProvider(
        responses=[
            LLMResponse(
                tool_calls=[
                    ToolCall(id="tools_1", name="tools_list", arguments={}),
                    ToolCall(id="tools_2", name="tools_list", arguments={}),
                ]
            ),
            LLMResponse(content="done"),
        ]
    )
    bridge = RecordingBridge()
    runtime = InteractionRuntime(app.runner)

    await runtime.handle(
        InteractionInbound(channel="tui", text="tools_list", source="local", conversation_key="local:test"),
        bridge=bridge,
    )

    tool_events = [
        (item.tool_call.call.id, item.tool_call.status)
        for outbound in bridge.outbounds
        for item in outbound.items
        if item.kind == "tool_call" and item.tool_call is not None
    ]
    assert tool_events == [
        ("tools_1", "running"),
        ("tools_2", "running"),
        ("tools_1", "ok"),
        ("tools_2", "ok"),
    ]


@pytest.mark.asyncio
async def test_output_result_set_merges_dicts_by_serial_order(tmp_path):
    agents = _copy_agents(tmp_path)
    _write_module(
        agents,
        "assistant/agent/output/result_first/module.py",
        "def process(ctx):\n"
        "    ctx.result.set({'one': 1, 'same': 'first'})\n",
    )
    _write_module(
        agents,
        "assistant/agent/output/result_second/module.py",
        "def process(ctx):\n"
        "    ctx.result.set({'two': 2, 'same': 'second'})\n",
    )
    _write_slot(agents, "assistant/agent/output/result_first/slot.yaml", _slot_text())
    _write_slot(agents, "assistant/agent/output/result_second/slot.yaml", _slot_text())
    _write_pipeline(agents, "output", serial=["base_output", "result_first", "result_second"])
    app = create_app(home=tmp_path / "home", provider_name="fake", agents_root=agents)
    app.runner.provider = RecordingProvider(default="main")

    result = await app.runner.run_turn("hello")

    assert _delivery_texts(result) == ["main"]
    assert result.agent_result == {"one": 1, "same": "second", "two": 2}


@pytest.mark.asyncio
async def test_output_result_set_non_dict_replaces_current_result(tmp_path):
    agents = _copy_agents(tmp_path)
    _write_module(
        agents,
        "assistant/agent/output/result_dict/module.py",
        "def process(ctx):\n"
        "    ctx.result.set({'one': 1})\n",
    )
    _write_module(
        agents,
        "assistant/agent/output/result_replace/module.py",
        "def process(ctx):\n"
        "    ctx.result.set('done')\n",
    )
    _write_slot(agents, "assistant/agent/output/result_dict/slot.yaml", _slot_text())
    _write_slot(agents, "assistant/agent/output/result_replace/slot.yaml", _slot_text())
    _write_pipeline(agents, "output", serial=["base_output", "result_dict", "result_replace"])
    app = create_app(home=tmp_path / "home", provider_name="fake", agents_root=agents)
    app.runner.provider = RecordingProvider(default="main")

    result = await app.runner.run_turn("hello")

    assert _delivery_texts(result) == ["main"]
    assert result.agent_result == "done"


@pytest.mark.asyncio
async def test_agents_run_returns_child_result_without_auto_mirroring_delivery(tmp_path):
    agents = _copy_agents(tmp_path)
    _write_module(
        agents,
        "assistant/agent/output/agent_probe/module.py",
        "async def process(ctx):\n"
        "    result = await ctx.agents.run('evolver', 'child raw', context='CHILD_CTX')\n"
        "    ctx.output.send_text(\n"
        "        'run-result:' + result.content + ':' + str(len(result.deliveries)) + ':' + result.session_id,\n"
        "        history_policy='model_hidden',\n"
        "    )\n",
    )
    _write_slot(
        agents,
        "assistant/agent/output/agent_probe/slot.yaml",
        _slot_text(capabilities=["agents.run:evolver"]),
    )
    _write_pipeline(agents, "output", serial=["base_output", "agent_probe"])
    app = create_app(home=tmp_path / "home", provider_name="fake", agents_root=agents)
    provider = RecordingProvider(responses=["parent", "child"])
    app.runner.provider = provider

    result = await app.runner.run_turn("hello")

    assert _delivery_texts(result)[0] == "parent"
    assert _delivery_texts(result)[1].startswith("run-result:child:1:session_child_")
    child_session_id = _delivery_texts(result)[1].rsplit(":", 1)[1]
    parent_messages = app.session_runtime.read_messages(app.runner.session_id)
    assert [(message.role, message.content) for message in parent_messages] == [
        ("user", "hello"),
        ("assistant", "parent"),
        ("assistant", _delivery_texts(result)[1]),
    ]
    child_messages = app.session_runtime.read_messages(child_session_id)
    assert [(message.role, message.content) for message in child_messages] == [
        ("user", "child raw"),
        ("assistant", "child"),
    ]
    child_request_text = "\n".join(message.content for message in provider.requests[1].messages)
    assert "CHILD_CTX" in child_request_text
    assert all("CHILD_CTX" not in message.content for message in child_messages)


@pytest.mark.asyncio
async def test_agents_run_returns_explicit_child_result_to_parent_module(tmp_path):
    agents = _copy_agents(tmp_path)
    _write_module(
        agents,
        "evolver/agent/output/result_only/module.py",
        "def process(ctx):\n"
        "    ctx.result.set({'answer': 'structured', 'count': 1})\n",
    )
    _write_slot(agents, "evolver/agent/output/result_only/slot.yaml", _slot_text())
    _write_pipeline(agents, "output", serial=["result_only"], core_id="evolver")
    _write_module(
        agents,
        "assistant/agent/output/agent_probe/module.py",
        "async def process(ctx):\n"
        "    result = await ctx.agents.run('evolver', 'child raw', output_slots=['result_only'])\n"
        "    ctx.output.send_text('child-result:' + result.result['answer'], history_policy='model_hidden')\n",
    )
    _write_slot(
        agents,
        "assistant/agent/output/agent_probe/slot.yaml",
        _slot_text(capabilities=["agents.run:evolver"]),
    )
    _write_pipeline(agents, "output", serial=["base_output", "agent_probe"])
    app = create_app(home=tmp_path / "home", provider_name="fake", agents_root=agents)
    app.runner.provider = RecordingProvider(responses=["parent", "child"])

    result = await app.runner.run_turn("hello")

    assert _delivery_texts(result) == ["parent", "child-result:structured"]


@pytest.mark.asyncio
async def test_agents_run_defaults_to_base_slots_and_skips_child_bootstrap(tmp_path):
    agents = _copy_agents(tmp_path)
    _write_module(
        agents,
        "evolver/agent/bootstrap/child_boot/module.py",
        "def process(ctx):\n"
        "    ctx.bootstrap.add('CHILD_BOOT')\n",
    )
    _write_slot(agents, "evolver/agent/bootstrap/child_boot/slot.yaml", _slot_text())
    _write_pipeline(agents, "bootstrap", serial=["child_boot"], core_id="evolver")
    _write_module(
        agents,
        "evolver/agent/input/child_prefix/module.py",
        "def process(ctx):\n"
        "    ctx.input.add_context('CHILD_PREFIX', role='system')\n",
    )
    _write_slot(agents, "evolver/agent/input/child_prefix/slot.yaml", _slot_text())
    _write_pipeline(agents, "input", serial=["child_prefix", "base_input"], core_id="evolver")
    _write_module(
        agents,
        "evolver/agent/output/child_extra/module.py",
        "def process(ctx):\n"
        "    ctx.output.send_text('child-extra')\n",
    )
    _write_slot(agents, "evolver/agent/output/child_extra/slot.yaml", _slot_text())
    _write_pipeline(agents, "output", serial=["base_output", "child_extra"], core_id="evolver")
    _write_module(
        agents,
        "assistant/agent/output/agent_probe/module.py",
        "async def process(ctx):\n"
        "    result = await ctx.agents.run('evolver', 'child raw')\n"
        "    ctx.output.send_text('child-result:' + result.content + ':' + result.session_id, history_policy='model_hidden')\n",
    )
    _write_slot(
        agents,
        "assistant/agent/output/agent_probe/slot.yaml",
        _slot_text(capabilities=["agents.run:evolver"]),
    )
    _write_pipeline(agents, "output", serial=["base_output", "agent_probe"])
    app = create_app(home=tmp_path / "home", provider_name="fake", agents_root=agents)
    provider = RecordingProvider(responses=["parent", "child"])
    app.runner.provider = provider

    result = await app.runner.run_turn("hello")

    assert _delivery_texts(result)[0] == "parent"
    assert _delivery_texts(result)[1].startswith("child-result:child:session_child_")
    child_session_id = _delivery_texts(result)[1].rsplit(":", 1)[1]
    assert not app.session_runtime.bootstrap_context_exists(child_session_id)
    child_request_text = "\n".join(message.content for message in provider.requests[1].messages)
    assert "CHILD_BOOT" not in child_request_text
    assert "CHILD_PREFIX" not in child_request_text


@pytest.mark.asyncio
async def test_agents_run_all_slots_preserves_serial_and_parallel_child_pipeline(tmp_path):
    agents = _copy_agents(tmp_path)
    _write_module(
        agents,
        "evolver/agent/input/child_prefix/module.py",
        "def process(ctx):\n"
        "    ctx.input.add_context('CHILD_PREFIX', role='system')\n",
    )
    _write_slot(agents, "evolver/agent/input/child_prefix/slot.yaml", _slot_text())
    _write_pipeline(agents, "input", serial=["child_prefix", "base_input"], core_id="evolver")
    _write_module(
        agents,
        "evolver/agent/output/child_serial/module.py",
        "def process(ctx):\n"
        "    ctx.output.send_text('child-serial')\n",
    )
    _write_slot(agents, "evolver/agent/output/child_serial/slot.yaml", _slot_text())
    _write_module(
        agents,
        "evolver/agent/output/child_parallel/module.py",
        "def process(ctx):\n"
        "    ctx.output.send_text('child-parallel')\n",
    )
    _write_slot(agents, "evolver/agent/output/child_parallel/slot.yaml", _slot_text())
    _write_pipeline(
        agents,
        "output",
        serial=["base_output", "child_serial"],
        parallel=["child_parallel"],
        core_id="evolver",
    )
    _write_module(
        agents,
        "assistant/agent/output/agent_probe/module.py",
        "async def process(ctx):\n"
        "    result = await ctx.agents.run('evolver', 'child raw', input_slots='all', output_slots='all')\n"
        "    parallel = ','.join(result.metadata['child_agent_slots']['output_slots']['parallel'])\n"
        "    ctx.output.send_text('|'.join(delivery.text for delivery in result.deliveries) + ':' + parallel, history_policy='model_hidden')\n",
    )
    _write_slot(
        agents,
        "assistant/agent/output/agent_probe/slot.yaml",
        _slot_text(capabilities=["agents.run:evolver"]),
    )
    _write_pipeline(agents, "output", serial=["base_output", "agent_probe"])
    app = create_app(home=tmp_path / "home", provider_name="fake", agents_root=agents)
    provider = RecordingProvider(responses=["parent", "child"])
    app.runner.provider = provider

    result = await app.runner.run_turn("hello")

    assert _delivery_texts(result) == ["parent", "child|child-serial:child_parallel"]
    child_request_text = "\n".join(message.content for message in provider.requests[1].messages)
    assert "CHILD_PREFIX" in child_request_text


@pytest.mark.asyncio
async def test_agents_run_filters_child_slots_in_pipeline_order(tmp_path):
    agents = _copy_agents(tmp_path)
    for slot_id, text in [("child_one", "one"), ("child_two", "two")]:
        _write_module(
            agents,
            f"evolver/agent/output/{slot_id}/module.py",
            "def process(ctx):\n"
            f"    ctx.output.send_text('{text}')\n",
        )
        _write_slot(agents, f"evolver/agent/output/{slot_id}/slot.yaml", _slot_text())
    _write_pipeline(agents, "output", serial=["base_output", "child_one", "child_two"], core_id="evolver")
    _write_module(
        agents,
        "assistant/agent/output/agent_probe/module.py",
        "async def process(ctx):\n"
        "    result = await ctx.agents.run('evolver', 'child raw', output_slots=['child_two', 'base_output'])\n"
        "    ctx.output.send_text('|'.join(delivery.text for delivery in result.deliveries), history_policy='model_hidden')\n",
    )
    _write_slot(
        agents,
        "assistant/agent/output/agent_probe/slot.yaml",
        _slot_text(capabilities=["agents.run:evolver"]),
    )
    _write_pipeline(agents, "output", serial=["base_output", "agent_probe"])
    app = create_app(home=tmp_path / "home", provider_name="fake", agents_root=agents)
    app.runner.provider = RecordingProvider(responses=["parent", "child"])

    result = await app.runner.run_turn("hello")

    assert _delivery_texts(result) == ["parent", "child|two"]


@pytest.mark.asyncio
async def test_agents_run_can_enable_child_bootstrap(tmp_path):
    agents = _copy_agents(tmp_path)
    _write_module(
        agents,
        "evolver/agent/bootstrap/child_boot/module.py",
        "def process(ctx):\n"
        "    ctx.bootstrap.add('CHILD_BOOT')\n",
    )
    _write_slot(agents, "evolver/agent/bootstrap/child_boot/slot.yaml", _slot_text())
    _write_pipeline(agents, "bootstrap", serial=["child_boot"], core_id="evolver")
    _write_module(
        agents,
        "assistant/agent/output/agent_probe/module.py",
        "async def process(ctx):\n"
        "    result = await ctx.agents.run('evolver', 'child raw', use_bootstrap=True)\n"
        "    ctx.output.send_text('child-result:' + result.content + ':' + result.session_id, history_policy='model_hidden')\n",
    )
    _write_slot(
        agents,
        "assistant/agent/output/agent_probe/slot.yaml",
        _slot_text(capabilities=["agents.run:evolver"]),
    )
    _write_pipeline(agents, "output", serial=["base_output", "agent_probe"])
    app = create_app(home=tmp_path / "home", provider_name="fake", agents_root=agents)
    provider = RecordingProvider(responses=["parent", "child"])
    app.runner.provider = provider

    result = await app.runner.run_turn("hello")

    child_request_text = "\n".join(message.content for message in provider.requests[1].messages)
    assert "CHILD_BOOT" in child_request_text
    child_session_id = _delivery_texts(result)[1].rsplit(":", 1)[1]
    assert app.session_runtime.read_bootstrap_context(child_session_id) == "CHILD_BOOT"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("argument", "message"),
    [
        ("input_slots=['missing']", "unknown input slot id: missing"),
        ("input_slots=['base_input', 'base_input']", "duplicate input slot id: base_input"),
        ("input_slots=['detached']", "input slot id is not in the active pipeline: detached"),
    ],
)
async def test_agents_run_rejects_invalid_child_slot_selection(tmp_path, argument, message):
    agents = _copy_agents(tmp_path)
    _write_module(
        agents,
        "evolver/agent/input/detached/module.py",
        "def process(ctx):\n"
        "    ctx.input.add_context('DETACHED', role='system')\n",
    )
    _write_slot(agents, "evolver/agent/input/detached/slot.yaml", _slot_text())
    _write_module(
        agents,
        "assistant/agent/output/agent_probe/module.py",
        "async def process(ctx):\n"
        f"    await ctx.agents.run('evolver', 'child raw', {argument})\n",
    )
    _write_slot(
        agents,
        "assistant/agent/output/agent_probe/slot.yaml",
        _slot_text(failure_policy="hard", capabilities=["agents.run:evolver"]),
    )
    _write_pipeline(agents, "output", serial=["base_output", "agent_probe"])
    app = create_app(home=tmp_path / "home", provider_name="fake", agents_root=agents)
    app.runner.provider = RecordingProvider(responses=["parent"])

    with pytest.raises(ValueError, match=message):
        await app.runner.run_turn("hello")


@pytest.mark.asyncio
async def test_agents_spawn_rejects_invalid_child_slot_selection(tmp_path):
    agents = _copy_agents(tmp_path)
    _write_module(
        agents,
        "assistant/agent/output/spawn_probe/module.py",
        "def process(ctx):\n"
        "    ctx.agents.spawn('evolver', 'spawn raw', output_slots=['missing'])\n",
    )
    _write_slot(
        agents,
        "assistant/agent/output/spawn_probe/slot.yaml",
        _slot_text(failure_policy="hard", capabilities=["agents.spawn:evolver"]),
    )
    _write_pipeline(agents, "output", serial=["base_output", "spawn_probe"])
    app = create_app(home=tmp_path / "home", provider_name="fake", agents_root=agents)
    app.runner.provider = RecordingProvider(responses=["parent"])

    with pytest.raises(ValueError, match="unknown output slot id: missing"):
        await app.runner.run_turn("hello")


@pytest.mark.asyncio
async def test_agents_spawn_returns_handle_without_waiting_for_child_turn(tmp_path):
    agents = _copy_agents(tmp_path)
    _write_module(
        agents,
        "assistant/agent/output/spawn_probe/module.py",
        "def process(ctx):\n"
        "    handle = ctx.agents.spawn('evolver', 'spawn raw', context='SPAWN_CTX')\n"
        "    ctx.output.send_text(f'spawn:{handle.status}:{handle.core_id}', history_policy='model_hidden')\n",
    )
    _write_slot(
        agents,
        "assistant/agent/output/spawn_probe/slot.yaml",
        _slot_text(capabilities=["agents.spawn:evolver"]),
    )
    _write_pipeline(agents, "output", serial=["base_output", "spawn_probe"])
    app = create_app(home=tmp_path / "home", provider_name="fake", agents_root=agents)

    class BlockingChildProvider(RecordingProvider):
        def __init__(self):
            super().__init__()
            self.child_started = asyncio.Event()
            self.release_child = asyncio.Event()

        async def complete(self, request):
            self.requests.append(request)
            if len(self.requests) == 1:
                return LLMResponse(content="parent")
            self.child_started.set()
            await self.release_child.wait()
            return LLMResponse(content="spawn child")

    provider = BlockingChildProvider()
    app.runner.provider = provider

    result = await asyncio.wait_for(app.runner.run_turn("hello"), timeout=0.2)

    assert _delivery_texts(result) == ["parent", "spawn:running:evolver"]
    await asyncio.wait_for(provider.child_started.wait(), timeout=0.2)
    assert not any(event["type"] == "agent_spawn.completed" for event in app.runner.event_log.tail(50))
    provider.release_child.set()
    await app.runner.drain_background_tasks()
    agent_tasks = app.task_worker.list_tasks(kind="agent.spawn")
    assert len(agent_tasks) == 1
    assert agent_tasks[0].status == "succeeded"
    assert agent_tasks[0].metadata["requested_child_agent_slots"] == {
        "input_slots": ["base_input"],
        "output_slots": ["base_output"],
        "use_bootstrap": False,
    }
    assert agent_tasks[0].metadata["resolved_child_agent_slots"] == {
        "input_slots": {"serial": ["base_input"], "parallel": []},
        "output_slots": {"serial": ["base_output"], "parallel": []},
        "use_bootstrap": False,
    }
    assert app.task_worker.pending_events_for_session(app.runner.session_id)[0].task_id == agent_tasks[0].task_id
    messages = app.session_runtime.read_messages(app.runner.session_id)
    assert [message.content for message in messages if message.role == "assistant"] == [
        "parent",
        "spawn:running:evolver",
    ]
    assert any(event["type"] == "agent_spawn.completed" for event in app.runner.event_log.tail(50))


@pytest.mark.asyncio
async def test_agents_spawn_marks_task_blocked_when_child_needs_user(tmp_path):
    agents = _copy_agents(tmp_path)
    evolver_manifest = agents / "evolver" / "agent.yaml"
    raw = yaml.safe_load(evolver_manifest.read_text(encoding="utf-8"))
    raw["tools"]["metadata"]["clarify"] = {"enabled": True}
    evolver_manifest.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    _write_module(
        agents,
        "assistant/agent/output/spawn_probe/module.py",
        "def process(ctx):\n"
        "    handle = ctx.agents.spawn('evolver', 'spawn raw')\n"
        "    ctx.output.send_text(f'spawn:{handle.status}:{handle.core_id}', history_policy='model_hidden')\n",
    )
    _write_slot(
        agents,
        "assistant/agent/output/spawn_probe/slot.yaml",
        _slot_text(capabilities=["agents.spawn:evolver"]),
    )
    _write_pipeline(agents, "output", serial=["base_output", "spawn_probe"])
    app = create_app(home=tmp_path / "home", provider_name="fake", agents_root=agents)
    app.runner.provider = RecordingProvider(
        responses=[
            "parent",
            LLMResponse(tool_calls=[ToolCall(id="clarify_1", name="clarify", arguments={"question": "Need input?"})]),
        ]
    )

    await app.runner.run_turn("hello")
    await app.runner.drain_background_tasks()

    agent_tasks = app.task_worker.list_tasks(kind="agent.spawn")
    assert len(agent_tasks) == 1
    assert agent_tasks[0].status == "blocked_needs_user"
    assert app.task_worker.pending_events_for_session(app.runner.session_id)[0].status == "blocked_needs_user"
    assert any(event["type"] == "agent_spawn.blocked" for event in app.runner.event_log.tail(50))


@pytest.mark.asyncio
async def test_child_result_artifact_can_be_sent_by_parent(tmp_path):
    agents = _copy_agents(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "voice.ogg").write_text("RAW-AUDIO-CONTENT", encoding="utf-8")
    _write_module(
        agents,
        "evolver/agent/output/tts_result/module.py",
        "def process(ctx):\n"
        "    ctx.result.set({\n"
        "        'audio': {\n"
        "            'path': 'voice.ogg',\n"
        "            'kind': 'audio',\n"
        "            'media_type': 'audio/ogg',\n"
        "            'summary': 'voice note',\n"
        "        },\n"
        "        'caption': 'voice ready',\n"
        "        'transcript': 'short voice',\n"
        "    })\n",
    )
    _write_slot(agents, "evolver/agent/output/tts_result/slot.yaml", _slot_text())
    _write_pipeline(agents, "output", serial=["tts_result"], core_id="evolver")
    _write_module(
        agents,
        "assistant/agent/output/tts_parent/module.py",
        "async def process(ctx):\n"
        "    result = await ctx.agents.run('evolver', 'make voice', output_slots=['tts_result'])\n"
        "    audio = result.result['audio']\n"
        "    ctx.output.send_audio(\n"
        "        audio['path'],\n"
        "        caption=result.result['caption'],\n"
        "        media_type=audio.get('media_type'),\n"
        "        summary=audio.get('summary'),\n"
        "        artifact_metadata=audio.get('metadata'),\n"
        "        history_policy='model_hidden',\n"
        "        history_text='voice result ready',\n"
        "    )\n",
    )
    _write_slot(
        agents,
        "assistant/agent/output/tts_parent/slot.yaml",
        _slot_text(capabilities=["agents.run:evolver"]),
    )
    _write_pipeline(agents, "output", serial=["base_output", "tts_parent"])
    app = create_app(home=tmp_path / "home", provider_name="fake", agents_root=agents, workspace=workspace)
    app.runner.provider = RecordingProvider(responses=["parent", "child"])

    result = await app.runner.run_turn("hello")

    assert _delivery_texts(result)[0] == "parent"
    assert _delivery_texts(result)[1].startswith("voice ready")
    assert result.deliveries[1].blocks[-1]["type"] == "audio"
    assert result.deliveries[1].blocks[-1]["artifact"]["resolved_path"] == str(workspace / "voice.ogg")
    parent_messages = app.session_runtime.read_messages(app.runner.session_id)
    assert [message.content for message in parent_messages if message.role == "assistant"] == [
        "parent",
        "voice result ready",
    ]


@pytest.mark.asyncio
async def test_output_result_rejects_non_serializable_value(tmp_path):
    agents = _copy_agents(tmp_path)
    _write_module(
        agents,
        "assistant/agent/output/bad_result/module.py",
        "def process(ctx):\n"
        "    ctx.result.set({'bad': object()})\n",
    )
    _write_slot(agents, "assistant/agent/output/bad_result/slot.yaml", _slot_text())
    _write_pipeline(agents, "output", serial=["base_output", "bad_result"])
    app = create_app(home=tmp_path / "home", provider_name="fake", agents_root=agents)
    app.runner.provider = RecordingProvider(default="main")

    result = await app.runner.run_turn("hello")

    assert _delivery_texts(result) == ["main"]
    assert result.agent_result is None
    assert any(
        event["type"] == "module.failed"
        and event["slot"] == "agent/output/bad_result"
        and "JSON-compatible" in event["error"]
        for event in app.runner.event_log.tail(30)
    )


@pytest.mark.asyncio
async def test_result_set_rejects_artifact_path_outside_workspace_or_session(tmp_path):
    agents = _copy_agents(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside_path = tmp_path / "outside.ogg"
    _write_module(
        agents,
        "assistant/agent/output/bad_artifact/module.py",
        "def process(ctx):\n"
        f"    ctx.result.set({{'audio': {{'path': {str(outside_path)!r}, 'kind': 'audio'}}}})\n",
    )
    _write_slot(agents, "assistant/agent/output/bad_artifact/slot.yaml", _slot_text())
    _write_pipeline(agents, "output", serial=["base_output", "bad_artifact"])
    app = create_app(home=tmp_path / "home", provider_name="fake", agents_root=agents, workspace=workspace)
    app.runner.provider = RecordingProvider(default="main")

    result = await app.runner.run_turn("hello")

    assert _delivery_texts(result) == ["main"]
    assert result.agent_result is None
    assert any(
        event["type"] == "module.failed"
        and event["slot"] == "agent/output/bad_artifact"
        and "outside the workspace or session" in event["error"]
        for event in app.runner.event_log.tail(30)
    )


@pytest.mark.asyncio
async def test_deliver_attachment_uses_artifact_reference_in_history(tmp_path):
    agents = _copy_agents(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "voice.ogg").write_text("RAW-AUDIO-CONTENT", encoding="utf-8")
    _write_module(
        agents,
        "assistant/agent/output/audio/module.py",
        "def process(ctx):\n"
        "    ctx.output.send_audio(\n"
        "        'voice.ogg',\n"
        "        caption='voice ready',\n"
        "        media_type='audio/ogg',\n"
        "        summary='voice note',\n"
        "        artifact_metadata={'source': 'unit'},\n"
        "        history_text='voice ready: voice note',\n"
        "    )\n",
    )
    _write_slot(agents, "assistant/agent/output/audio/slot.yaml", _slot_text())
    _write_pipeline(agents, "output", serial=["base_output", "audio"])
    app = create_app(home=tmp_path / "home", provider_name="fake", agents_root=agents, workspace=workspace)
    app.runner.provider = RecordingProvider(default="main")

    result = await app.runner.run_turn("hello")

    assert _delivery_texts(result)[0] == "main"
    assert _delivery_texts(result)[1].startswith("voice ready")
    assistant_history = [
        message.content
        for message in app.session_runtime.read_messages(app.runner.session_id)
        if message.role == "assistant"
    ]
    assert assistant_history[-1] == "voice ready: voice note"
    assert "RAW-AUDIO-CONTENT" not in assistant_history[-1]
    assert result.deliveries[-1].blocks[-1]["type"] == "audio"
    assert result.deliveries[-1].blocks[-1]["artifact"]["media_type"] == "audio/ogg"
    assert result.deliveries[-1].blocks[-1]["artifact"]["summary"] == "voice note"
    assert result.deliveries[-1].blocks[-1]["artifact"]["metadata"] == {"source": "unit"}
    assert result.deliveries[-1].blocks[-1]["artifact"]["resolved_path"] == str(workspace / "voice.ogg")
    assert not (app.home / "sessions").exists()


@pytest.mark.asyncio
async def test_send_artifact_rejects_descriptor_dict(tmp_path):
    agents = _copy_agents(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "voice.ogg").write_text("RAW-AUDIO-CONTENT", encoding="utf-8")
    _write_module(
        agents,
        "assistant/agent/output/audio/module.py",
        "def process(ctx):\n"
        "    ctx.output.send_audio({'path': 'voice.ogg', 'kind': 'audio'})\n",
    )
    _write_slot(agents, "assistant/agent/output/audio/slot.yaml", _slot_text())
    _write_pipeline(agents, "output", serial=["base_output", "audio"])
    app = create_app(home=tmp_path / "home", provider_name="fake", agents_root=agents, workspace=workspace)
    app.runner.provider = RecordingProvider(default="main")

    result = await app.runner.run_turn("hello")

    assert _delivery_texts(result) == ["main"]
    assert any(
        event["type"] == "module.failed"
        and event["slot"] == "agent/output/audio"
        and "pass summary=... and artifact_metadata=..." in event["error"]
        for event in app.runner.event_log.tail(30)
    )
