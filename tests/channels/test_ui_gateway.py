import asyncio
from pathlib import Path

import pytest

from demiurge.app import create_app
from demiurge.providers import LLMResponse, ToolCall
from demiurge.runtime.interactions import InteractionDelivery, InteractionItem, InteractionOutbound, ToolInteractionRecord, UserPromptRequest
from demiurge.security.approval import ApprovalRequest
from demiurge.sdk import ToolResult
from demiurge.slash import specs_for_surface
from demiurge.tools.records import ToolExecutionRecord
from demiurge.ui_gateway import TuiInteractionBridge, parse_approval_response, parse_tool_display_level
from demiurge.util import write_json


class EventSink:
    def __init__(self):
        self.items = []

    async def __call__(self, event, payload):
        self.items.append((event, payload))

    def payloads(self, event):
        return [payload for name, payload in self.items if name == event]

    def texts(self):
        values = []
        for payload in self.payloads("interaction.deliver"):
            for delivery in payload.get("deliveries", []):
                values.append(delivery.get("text") or delivery.get("fallback_text") or "")
            for tool in payload.get("tool_calls", []):
                values.append(str(tool))
            for tool in payload.get("tool_results", []):
                values.append(str(tool))
        return "\n".join(values)


class BlockingProvider:
    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.requests = []

    async def complete(self, request):
        self.requests.append(request)
        if len(self.requests) == 1:
            self.started.set()
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                self.cancelled.set()
                raise
            return LLMResponse(content="[slow] first")
        user_text = next((message.content for message in reversed(request.messages) if message.role == "user"), "")
        return LLMResponse(content=f"[next] {user_text}")


class RecordingEchoProvider:
    def __init__(self):
        self.requests = []

    async def complete(self, request):
        self.requests.append(request)
        user_text = next((message.content for message in reversed(request.messages) if message.role == "user"), "")
        return LLMResponse(content=f"[echo] {user_text}")


class YieldUntilProvider:
    def __init__(self, task_id: str):
        self.task_id = task_id
        self.requests = []
        self.first_request = asyncio.Event()

    async def complete(self, request):
        self.requests.append(request)
        if len(self.requests) == 1:
            self.first_request.set()
            return LLMResponse(
                tool_calls=[
                    ToolCall(
                        id="yield_1",
                        name="yield_until",
                        arguments={"task_id": self.task_id, "timeout_seconds": 2},
                    )
                ]
            )
        return LLMResponse(content="[waited]")


async def _wait_for(predicate, *, timeout: float = 2.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition timed out")


async def _complete_background_task(app, *, summary: str = "background complete"):
    async def task(ctx):
        ctx.append_log("background tail")
        return summary

    record = app.task_worker.start_task(
        kind="terminal.exec",
        owner_session_id=app.runner.session_id,
        owner_turn_id="turn_origin",
        source_tool="test",
        task_factory=task,
    )
    await app.task_worker.wait(record.task_id, timeout_seconds=1)
    return record


def test_parse_approval_response():
    assert parse_approval_response("y").value == "allow"
    assert parse_approval_response("1").value == "allow"
    assert parse_approval_response("a").value == "always_allow_for_session"
    assert parse_approval_response("2").value == "always_allow_for_session"
    assert parse_approval_response("n").value == "deny"
    assert parse_approval_response("3").value == "deny"


def test_parse_tool_display_level():
    assert parse_tool_display_level("quiet") == "quiet"
    assert parse_tool_display_level("off") == "quiet"
    assert parse_tool_display_level("summary") == "summary"
    assert parse_tool_display_level("brief") == "summary"
    assert parse_tool_display_level("full") == "full"
    assert parse_tool_display_level("verbose") == "full"
    assert parse_tool_display_level("bad") is None


@pytest.mark.asyncio
async def test_tui_bridge_submit_uses_interaction_runtime(tmp_path):
    sink = EventSink()
    app = create_app(home=tmp_path / "home", provider_name="fake")
    bridge = TuiInteractionBridge(app, emit=sink)

    await bridge.initialize()
    await bridge.submit("hello")
    await bridge.wait_for_idle()

    assert ("interaction.message", {"role": "user", "text": "hello"}) in sink.items
    assert "[fake] hello" in sink.texts()
    received = next(event for event in app.runner.event_log.tail(30) if event["type"] == "message.received")
    assert received["channel"] == "tui"
    assert received["source"] == "local"


@pytest.mark.asyncio
async def test_tui_bridge_ready_includes_tui_slash_command_catalog(tmp_path):
    sink = EventSink()
    app = create_app(home=tmp_path / "home", provider_name="fake")
    bridge = TuiInteractionBridge(app, emit=sink)

    await bridge.initialize()

    ready = sink.payloads("interaction.ready")[-1]
    commands = ready["slash_commands"]
    names = {command["name"] for command in commands}
    assert ready["core_revision"] == app.version_store.active_pointer("assistant").active_revision
    assert names == {spec.name for spec in specs_for_surface("tui")}
    assert "tool-display" in names
    assert "busy" in names
    assert "stop" not in names
    assert "queue" not in names
    assert {"name", "description", "group", "usage"} <= set(commands[0])
    assert ready["user_message_align"] == "left"
    assert ready["demiurge_theme_color"] == "#ff9afc"
    assert ready["user_theme_color"] == "#9cc9ff"
    assert sink.payloads("interaction.status")[-1]["user_message_align"] == "left"
    assert sink.payloads("interaction.status")[-1]["demiurge_theme_color"] == "#ff9afc"
    assert sink.payloads("interaction.status")[-1]["user_theme_color"] == "#9cc9ff"


@pytest.mark.asyncio
async def test_tui_bridge_ready_includes_host_user_message_align(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.yaml").write_text("ui:\n  user_message_align: right\n", encoding="utf-8")
    sink = EventSink()
    app = create_app(home=home, provider_name="fake")
    bridge = TuiInteractionBridge(app, emit=sink)

    await bridge.initialize()

    assert sink.payloads("interaction.ready")[-1]["user_message_align"] == "right"
    assert sink.payloads("interaction.status")[-1]["user_message_align"] == "right"


@pytest.mark.asyncio
async def test_tui_bridge_ready_includes_host_theme_colors(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.yaml").write_text("ui:\n  demiurge_theme_color: fac\n  user_theme_color: '#aabbcc'\n", encoding="utf-8")
    sink = EventSink()
    app = create_app(home=home, provider_name="fake")
    bridge = TuiInteractionBridge(app, emit=sink)

    await bridge.initialize()

    assert sink.payloads("interaction.ready")[-1]["demiurge_theme_color"] == "#ffaacc"
    assert sink.payloads("interaction.ready")[-1]["user_theme_color"] == "#aabbcc"
    assert sink.payloads("interaction.status")[-1]["demiurge_theme_color"] == "#ffaacc"
    assert sink.payloads("interaction.status")[-1]["user_theme_color"] == "#aabbcc"


@pytest.mark.asyncio
async def test_tui_bridge_uses_host_config_initial_busy_mode(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.yaml").write_text("channel:\n  busy_mode: queue\n", encoding="utf-8")
    sink = EventSink()
    app = create_app(home=home, provider_name="fake")
    bridge = TuiInteractionBridge(app, emit=sink)

    await bridge.initialize()

    assert bridge.busy_mode == "queue"
    assert sink.payloads("interaction.ready")[-1]["busy_mode"] == "queue"
    assert sink.payloads("interaction.status")[-1]["busy_mode"] == "queue"


@pytest.mark.asyncio
async def test_tui_bridge_tool_display_quiet_summary_full(tmp_path):
    quiet_sink = EventSink()
    quiet_app = create_app(home=tmp_path / "quiet", provider_name="fake")
    quiet = TuiInteractionBridge(quiet_app, emit=quiet_sink, tool_display="quiet")
    await quiet.submit("please use tools_list")
    await quiet.wait_for_idle()
    assert not any(payload.get("tool_calls") or payload.get("tool_results") for payload in quiet_sink.payloads("interaction.deliver"))
    assert "[fake] tool result received" in quiet_sink.texts()

    full_sink = EventSink()
    full_app = create_app(home=tmp_path / "full", provider_name="fake")
    full = TuiInteractionBridge(full_app, emit=full_sink, tool_display="full")
    await full.submit("please use tools_list")
    await full.wait_for_idle()
    tool_payloads = [payload for payload in full_sink.payloads("interaction.deliver") if payload.get("tool_calls")]
    assert tool_payloads
    assert tool_payloads[0]["tool_display"] == "full"
    assert tool_payloads[0]["tool_calls"][0]["name"] == "tools_list"
    assert tool_payloads[0]["tool_calls"][0]["status"] == "running"
    assert tool_payloads[-1]["tool_calls"][0]["status"] == "ok"
    assert tool_payloads[-1]["tool_calls"][0]["arguments"] == {}


@pytest.mark.asyncio
async def test_tui_bridge_preserves_item_order_between_delivery_and_tool_result(tmp_path):
    sink = EventSink()
    app = create_app(home=tmp_path / "home", provider_name="fake")
    bridge = TuiInteractionBridge(app, emit=sink, tool_display="summary")
    record = ToolExecutionRecord(
        call=ToolCall(name="tools_list", arguments={}, id="call_1"),
        result=ToolResult(content="tool summary", display_output="tool summary"),
    )

    await bridge.deliver(
        InteractionOutbound(
            channel="tui",
            items=[
                InteractionItem.delivery_item(InteractionDelivery(type="text", text="first")),
                InteractionItem.tool_result_item(record),
            ],
        )
    )

    payloads = sink.payloads("interaction.deliver")
    assert [bool(payload.get("deliveries")) for payload in payloads] == [True, False]
    assert [bool(payload.get("tool_calls")) for payload in payloads] == [False, True]
    assert payloads[0]["deliveries"][0]["text"] == "first"
    assert payloads[1]["tool_calls"][0]["name"] == "tools_list"


@pytest.mark.asyncio
async def test_tui_bridge_emits_tool_lifecycle_payloads(tmp_path):
    sink = EventSink()
    app = create_app(home=tmp_path / "home", provider_name="fake")
    bridge = TuiInteractionBridge(app, emit=sink, tool_display="summary")
    call = ToolCall(name="terminal", arguments={"command": "whoami"}, id="call_1")
    result = ToolResult(content="exit_code: 0\nstdout:\nalice\n", display_output="$ whoami\ncwd: .\nexit_code: 0\nstdout:\nalice\n")

    await bridge.deliver(
        InteractionOutbound(
            channel="tui",
            items=[
                InteractionItem.tool_call_item(ToolInteractionRecord.started(call)),
                InteractionItem.tool_call_item(ToolInteractionRecord.finished(ToolExecutionRecord(call=call, result=result))),
            ],
        )
    )

    payloads = [payload for payload in sink.payloads("interaction.deliver") if payload.get("tool_calls")]
    assert [tool["status"] for payload in payloads for tool in payload["tool_calls"]] == ["running", "ok"]
    assert payloads[0]["tool_calls"][0]["summary"] == "$ whoami"
    assert "$ whoami" in payloads[-1]["tool_calls"][-1]["summary"]


@pytest.mark.asyncio
async def test_tui_bridge_clarify_prompt_reply_starts_next_turn(tmp_path):
    script = tmp_path / "ask-script.json"
    write_json(
        script,
        [{"tool_calls": [{"id": "ask_1", "name": "clarify", "arguments": {"question": "Which path?", "choices": ["fast", "careful"]}}]}],
    )
    sink = EventSink()
    app = create_app(home=tmp_path / "home", provider_name="fake", fake_script=script)
    bridge = TuiInteractionBridge(app, emit=sink)

    await bridge.submit("ask me")
    await bridge.wait_for_idle()

    prompt = sink.payloads("interaction.prompt.request")[-1]
    assert prompt["question"] == "Which path?"
    assert prompt["choices"] == ["fast", "careful"]

    await bridge.reply_prompt(prompt["prompt_id"], "2")
    await bridge.wait_for_idle()

    assert ("interaction.message", {"role": "user", "text": "careful"}) in sink.items
    assert "[fake] careful" in sink.texts()


@pytest.mark.asyncio
async def test_tui_bridge_prompt_user_waits_for_reply(tmp_path):
    sink = EventSink()
    app = create_app(home=tmp_path / "home", provider_name="fake")
    bridge = TuiInteractionBridge(app, emit=sink)

    task = asyncio.create_task(
        bridge.prompt_user(UserPromptRequest(question="Pick one", choices=["a", "b"], metadata={"kind": "clarify"}))
    )
    await _wait_for(lambda: bool(sink.payloads("interaction.prompt.request")))
    prompt = sink.payloads("interaction.prompt.request")[-1]
    await bridge.reply_prompt(prompt["prompt_id"], "2")

    assert await task == "b"


@pytest.mark.asyncio
async def test_tui_bridge_approval_request_round_trip(tmp_path):
    sink = EventSink()
    app = create_app(home=tmp_path / "home", provider_name="fake")
    bridge = TuiInteractionBridge(app, emit=sink)
    request = ApprovalRequest(
        tool_name="terminal",
        tool_call_id="call_1",
        turn_id="turn_1",
        capability="terminal.exec",
        action="exec",
        risk="critical",
        summary="Run command",
        command="printf hello",
    )

    task = asyncio.create_task(bridge.request_approval(request))
    await _wait_for(lambda: bool(sink.payloads("interaction.approval.request")))
    payload = sink.payloads("interaction.approval.request")[-1]
    assert payload["request"]["tool_name"] == "terminal"

    await bridge.reply_approval(payload["approval_id"], "2")

    decision = await task
    assert decision.value == "always_allow_for_session"


@pytest.mark.asyncio
async def test_tui_bridge_commands_stay_python_side(tmp_path):
    sink = EventSink()
    app = create_app(home=tmp_path / "home", provider_name="fake")
    bridge = TuiInteractionBridge(app, emit=sink)

    assert (await bridge.command("/status"))["handled"] is True
    assert (await bridge.command("/tools"))["handled"] is True
    assert (await bridge.command("/busy queue"))["handled"] is True
    assert bridge.busy_mode == "queue"
    assert (await bridge.command("/tool-display full"))["handled"] is True
    assert bridge.tool_display == "full"

    output = sink.texts()
    assert "Status" in output
    assert "Tools" in output
    assert "busy mode: queue" in output
    assert "tool display: full" in output


@pytest.mark.asyncio
async def test_tui_bridge_session_resume_prompt(tmp_path):
    sink = EventSink()
    app = create_app(home=tmp_path / "home", provider_name="fake")
    bridge = TuiInteractionBridge(app, emit=sink)
    original_session = app.runner.session_id

    await bridge.submit("hello")
    await bridge.wait_for_idle()
    await bridge.command("/new")
    assert app.runner.session_id != original_session
    assert sink.payloads("interaction.history")[-1]["items"] == []
    await bridge.command("/resume")
    prompt = sink.payloads("interaction.prompt.request")[-1]
    assert prompt["kind"] == "resume"
    original_index = next(index for index, record in enumerate(prompt["records"], start=1) if record["session_id"] == original_session)

    await bridge.reply_prompt(prompt["prompt_id"], str(original_index))

    assert app.runner.session_id == original_session
    history = sink.payloads("interaction.history")[-1]
    assert history["session_id"] == original_session
    assert {"type": "message", "role": "user", "text": "hello"}.items() <= history["items"][0].items()
    assert any(item["type"] == "message" and item["role"] == "assistant" and "[fake] hello" in item["text"] for item in history["items"])
    assert f"resumed session: {original_session}" in sink.texts()


@pytest.mark.asyncio
async def test_tui_bridge_initialize_emits_existing_session_history(tmp_path):
    sink = EventSink()
    app = create_app(home=tmp_path / "home", provider_name="fake")
    bridge = TuiInteractionBridge(app, emit=sink)

    await bridge.submit("hello")
    await bridge.wait_for_idle()

    fresh_sink = EventSink()
    fresh_bridge = TuiInteractionBridge(app, emit=fresh_sink)
    await fresh_bridge.initialize()

    history = fresh_sink.payloads("interaction.history")[-1]
    assert history["session_id"] == app.runner.session_id
    assert any(item["type"] == "message" and item["role"] == "user" and item["text"] == "hello" for item in history["items"])


@pytest.mark.asyncio
async def test_tui_bridge_resume_history_includes_tool_cards(tmp_path):
    sink = EventSink()
    app = create_app(home=tmp_path / "home", provider_name="fake")
    bridge = TuiInteractionBridge(app, emit=sink, tool_display="full")
    original_session = app.runner.session_id

    await bridge.submit("please use tools_list")
    await bridge.wait_for_idle()
    await bridge.command("/new")
    await bridge.command(f"/resume {original_session}")

    history = sink.payloads("interaction.history")[-1]
    tool_items = [item for item in history["items"] if item["type"] == "tool"]
    assert tool_items
    tool = tool_items[0]["tools"][0]
    assert tool["name"] == "tools_list"
    assert tool["id"]
    assert tool["status"] == "ok"
    assert tool["arguments"] == {}
    assert "tools" in tool["summary"].lower()
    assert not any(item.get("role") == "tool" for item in history["items"])


@pytest.mark.asyncio
async def test_tui_bridge_resume_history_respects_quiet_tool_display(tmp_path):
    sink = EventSink()
    app = create_app(home=tmp_path / "home", provider_name="fake")
    bridge = TuiInteractionBridge(app, emit=sink, tool_display="quiet")
    original_session = app.runner.session_id

    await bridge.submit("please use tools_list")
    await bridge.wait_for_idle()
    await bridge.command("/new")
    await bridge.command(f"/resume {original_session}")

    history = sink.payloads("interaction.history")[-1]
    assert any(item["type"] == "message" and item["role"] == "user" for item in history["items"])
    assert not any(item["type"] == "tool" for item in history["items"])


@pytest.mark.asyncio
async def test_tui_bridge_renders_progress_and_background_delivery(tmp_path):
    sink = EventSink()
    app = create_app(home=tmp_path / "home", provider_name="fake")
    bridge = TuiInteractionBridge(app, emit=sink)

    outbound = InteractionOutbound(
        channel="tui",
        items=[
            InteractionItem.delivery_item(
                InteractionDelivery(
                    kind="progress",
                    type="text",
                    text="before sleep",
                    metadata={"slot": "agent/output/debug", "delivery": "immediate"},
                )
            ),
            InteractionItem.delivery_item(
                InteractionDelivery(
                    kind="message",
                    type="text",
                    text="after sleep",
                    metadata={"slot": "agent/output/debug", "background": True},
                )
            ),
        ],
    )
    await bridge.deliver(outbound)

    payload = sink.payloads("interaction.deliver")[-1]
    assert payload["deliveries"][0]["kind"] == "progress"
    assert payload["deliveries"][0]["metadata"]["delivery"] == "immediate"
    assert payload["deliveries"][1]["metadata"]["background"] is True


@pytest.mark.asyncio
async def test_tui_bridge_runs_background_completion_turn_when_idle(tmp_path):
    sink = EventSink()
    app = create_app(home=tmp_path / "home", provider_name="fake")
    provider = RecordingEchoProvider()
    app.runner.provider = provider
    bridge = TuiInteractionBridge(app, emit=sink)

    await bridge.initialize()
    record = await _complete_background_task(app)

    await _wait_for(lambda: len(provider.requests) == 1 and not bridge.running)

    user_text = next(message.content for message in provider.requests[0].messages if message.role == "user")
    assert "[SYSTEM: Background task event]" in user_text
    assert record.task_id in user_text
    assert "background complete" in user_text
    assert sink.payloads("interaction.message") == []


@pytest.mark.asyncio
async def test_tui_bridge_yield_until_consumes_background_completion_turn(tmp_path):
    sink = EventSink()
    app = create_app(home=tmp_path / "home", provider_name="fake")
    release = asyncio.Event()

    async def task(ctx):
        ctx.append_log("waiting")
        await release.wait()
        ctx.append_log("done")
        return "background complete"

    record = app.task_worker.start_task(
        kind="terminal.exec",
        owner_session_id=app.runner.session_id,
        owner_turn_id="turn_origin",
        source_tool="test",
        task_factory=task,
    )
    provider = YieldUntilProvider(record.task_id)
    app.runner.provider = provider
    bridge = TuiInteractionBridge(app, emit=sink)

    await bridge.initialize()
    await bridge.submit("wait for the task")
    await provider.first_request.wait()
    release.set()

    await _wait_for(lambda: len(provider.requests) >= 2 and not bridge.running)
    await asyncio.sleep(0.05)

    assert len(provider.requests) == 2
    assert bridge._queued_inputs.empty()
    assert app.task_worker.pending_events_for_session(app.runner.session_id) == []


@pytest.mark.asyncio
async def test_tui_bridge_prioritizes_user_input_over_pending_completion(tmp_path):
    sink = EventSink()
    app = create_app(home=tmp_path / "home", provider_name="fake")
    provider = BlockingProvider()
    app.runner.provider = provider
    bridge = TuiInteractionBridge(app, emit=sink)

    await bridge.initialize()
    await bridge.submit("first")
    await provider.started.wait()
    record = await _complete_background_task(app)
    await _wait_for(lambda: bridge._queued_inputs.qsize() == 1)

    accepted = await bridge.submit("second")

    assert accepted == {"accepted": True, "queued": True}
    await provider.cancelled.wait()
    await _wait_for(lambda: "[next]" in sink.texts() and not bridge.running)
    user_text = next(message.content for message in reversed(provider.requests[-1].messages) if message.role == "user")
    assert user_text.startswith("second")
    assert "[SYSTEM: Pending background task events merged into this user turn]" in user_text
    assert record.task_id in user_text


@pytest.mark.asyncio
async def test_tui_bridge_queue_mode_runs_next_input_after_current_turn(tmp_path):
    sink = EventSink()
    app = create_app(home=tmp_path / "home", provider_name="fake")
    provider = BlockingProvider()
    app.runner.provider = provider
    bridge = TuiInteractionBridge(app, emit=sink, busy_mode="queue")

    await bridge.submit("first")
    await provider.started.wait()
    await bridge.submit("second")

    assert provider.cancelled.is_set() is False

    provider.release.set()
    await _wait_for(
        lambda: len(provider.requests) == 2 and "[next] second" in sink.texts() and not bridge.running,
        timeout=10.0,
    )

    output = sink.texts()
    assert "[slow] first" in output
    assert "[next] second" in output


@pytest.mark.asyncio
async def test_tui_bridge_interrupt_mode_cancels_current_turn_and_runs_next(tmp_path):
    sink = EventSink()
    app = create_app(home=tmp_path / "home", provider_name="fake")
    provider = BlockingProvider()
    app.runner.provider = provider
    bridge = TuiInteractionBridge(app, emit=sink)

    await bridge.submit("first")
    await provider.started.wait()
    await bridge.submit("second")
    await provider.cancelled.wait()
    await _wait_for(lambda: "[next] second" in sink.texts() and not bridge.running)

    output = sink.texts()
    assert "interrupting current turn: new input" in output
    assert "turn interrupted" in output
    assert "[next] second" in output
