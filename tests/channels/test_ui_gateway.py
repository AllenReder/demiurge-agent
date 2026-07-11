import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest

from demiurge.app import create_app
from demiurge.providers import LLMResponse, ToolCall
from demiurge.runtime.interactions import InteractionDelivery, InteractionItem, InteractionOutbound, ToolInteractionRecord, UserPromptRequest
from demiurge.security.approval import ApprovalRequest, ApprovalScope
from demiurge.security.capabilities import CapabilitySnapshot
from demiurge.sdk import ToolResult
from demiurge.slash import specs_for_surface
from demiurge.tools.records import ToolExecutionRecord
from demiurge.ui_gateway import OperatorGatewayRuntime, parse_approval_response, parse_tool_display_level
from demiurge.ui_gateway.entry import _is_long_operator_request
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
        for payload in self.payloads("operator.deliver"):
            for delivery in payload.get("deliveries", []):
                values.append(delivery.get("text") or delivery.get("fallback_text") or "")
            for tool in payload.get("tool_calls", []):
                values.append(str(tool))
            for tool in payload.get("tool_results", []):
                values.append(str(tool))
        return "\n".join(values)


@pytest.mark.asyncio
async def test_tui_01_initialize_rejects_protocol_mismatch_before_gateway_start():
    from demiurge.ui_gateway.entry import _dispatch
    from demiurge.ui_gateway.protocol import TUI_BUILD_STAMP, TUI_PROTOCOL_VERSION

    class FakeGateway:
        def __init__(self):
            self.initialize_calls = 0

        async def initialize(self):
            self.initialize_calls += 1
            return {"status": "idle"}

    gateway = FakeGateway()

    with pytest.raises(ValueError, match="TUI protocol mismatch"):
        await _dispatch(
            gateway,
            "operator.initialize",
            {
                "protocol_version": TUI_PROTOCOL_VERSION + 1,
                "build_stamp": TUI_BUILD_STAMP,
            },
        )

    assert gateway.initialize_calls == 0


@pytest.mark.asyncio
async def test_tui_01_initialize_rejects_build_mismatch_before_gateway_start():
    from demiurge.ui_gateway.entry import _dispatch
    from demiurge.ui_gateway.protocol import TUI_PROTOCOL_VERSION

    class FakeGateway:
        def __init__(self):
            self.initialize_calls = 0

        async def initialize(self):
            self.initialize_calls += 1
            return {"status": "idle"}

    gateway = FakeGateway()

    with pytest.raises(ValueError, match="TUI build mismatch"):
        await _dispatch(
            gateway,
            "operator.initialize",
            {
                "protocol_version": TUI_PROTOCOL_VERSION,
                "build_stamp": "stale-interaction-bundle",
            },
        )

    assert gateway.initialize_calls == 0


@pytest.mark.asyncio
async def test_tui_01_initialize_returns_host_protocol_identity():
    from demiurge.ui_gateway.entry import _dispatch
    from demiurge.ui_gateway.protocol import TUI_BUILD_STAMP, TUI_PROTOCOL_VERSION

    class FakeGateway:
        def __init__(self):
            self.initialize_calls = 0

        async def initialize(self):
            self.initialize_calls += 1
            return {"status": "idle"}

    gateway = FakeGateway()

    result = await _dispatch(
        gateway,
        "operator.initialize",
        {
            "protocol_version": TUI_PROTOCOL_VERSION,
            "build_stamp": TUI_BUILD_STAMP,
        },
    )

    assert gateway.initialize_calls == 1
    assert result == {
        "status": "idle",
        "protocol_version": TUI_PROTOCOL_VERSION,
        "build_stamp": TUI_BUILD_STAMP,
    }


@pytest.mark.asyncio
async def test_tui_01_non_initialize_first_frame_requires_identity_handshake():
    from demiurge.ui_gateway.entry import TuiIdentityMismatch, _dispatch

    class FakeGateway:
        pass

    with pytest.raises(TuiIdentityMismatch, match="identity handshake required"):
        await _dispatch(FakeGateway(), "interaction.initialize", {})


@pytest.mark.asyncio
async def test_tui_01_gateway_waits_for_identity_before_initialize(monkeypatch):
    from demiurge.ui_gateway import entry
    from demiurge.ui_gateway.protocol import TUI_BUILD_STAMP, TUI_PROTOCOL_VERSION

    class FakeApp:
        def __init__(self):
            self.closed = False

        async def close(self):
            self.closed = True

    class FakeGateway:
        instance = None

        def __init__(self, app, **kwargs):
            self.app = app
            self.initialize_calls = 0
            self.should_exit = False
            FakeGateway.instance = self

        async def initialize(self):
            self.initialize_calls += 1
            return {"status": "idle"}

    class FakeEndpoint:
        instance = None

        def __init__(self):
            self.errors = []
            FakeEndpoint.instance = self

        async def write_event(self, event, payload):
            return None

        async def write_error(self, message_id, message, *, code="error"):
            self.errors.append((message_id, code, message))

        async def write_result(self, message_id, result=None):
            raise AssertionError("mismatched initialize must not return a result")

        async def iter_requests(self):
            yield {
                "id": 1,
                "method": "operator.initialize",
                "params": {
                    "protocol_version": TUI_PROTOCOL_VERSION + 1,
                    "build_stamp": TUI_BUILD_STAMP,
                },
            }

    app = FakeApp()
    monkeypatch.setattr(entry, "create_app", lambda **kwargs: app)
    monkeypatch.setattr(entry, "OperatorGatewayRuntime", FakeGateway)
    monkeypatch.setattr(entry, "NdjsonRpcEndpoint", FakeEndpoint)

    exit_code = await entry.async_main(["--config-json", "{}"])

    assert FakeGateway.instance.initialize_calls == 0
    assert FakeEndpoint.instance.errors == [
        (1, "protocol_mismatch", "TUI protocol mismatch: expected 1, got 2")
    ]
    assert exit_code == 2
    assert app.closed is True


@pytest.mark.asyncio
async def test_ui_01_gateway_initialize_failure_is_fatal_startup_error(monkeypatch):
    from demiurge.ui_gateway import entry
    from demiurge.ui_gateway.protocol import TUI_BUILD_STAMP, TUI_PROTOCOL_VERSION

    class FakeApp:
        def __init__(self):
            self.closed = False

        async def close(self):
            self.closed = True

    class FakeGateway:
        def __init__(self, app, **kwargs):
            self.app = app
            self.should_exit = False

        async def initialize(self):
            raise RuntimeError("initialize failed")

    class FakeEndpoint:
        instance = None

        def __init__(self):
            self.events = []
            FakeEndpoint.instance = self

        async def write_event(self, event, payload):
            self.events.append((event, payload))

        async def write_error(self, message_id, message, *, code="error"):
            raise AssertionError("fatal initialize failure must be an operator event")

        async def write_result(self, message_id, result=None):
            raise AssertionError("fatal initialize failure must not return a result")

        async def iter_requests(self):
            yield {
                "id": 1,
                "method": "operator.initialize",
                "params": {
                    "protocol_version": TUI_PROTOCOL_VERSION,
                    "build_stamp": TUI_BUILD_STAMP,
                },
            }

    app = FakeApp()
    monkeypatch.setattr(entry, "create_app", lambda **kwargs: app)
    monkeypatch.setattr(entry, "OperatorGatewayRuntime", FakeGateway)
    monkeypatch.setattr(entry, "NdjsonRpcEndpoint", FakeEndpoint)

    exit_code = await entry.async_main(["--config-json", "{}"])

    assert exit_code == 1
    assert FakeEndpoint.instance.events == [
        (
            "operator.error",
            {
                "message": "initialize failed",
                "method": "operator.initialize",
                "source": "gateway_startup",
            },
        )
    ]
    assert app.closed is True


@pytest.mark.asyncio
async def test_ui_01_gateway_constructor_failure_closes_created_app(monkeypatch):
    from demiurge.ui_gateway import entry

    class FakeApp:
        def __init__(self):
            self.closed = False

        async def close(self):
            self.closed = True

    class FakeEndpoint:
        instance = None

        def __init__(self):
            self.events = []
            FakeEndpoint.instance = self

        async def write_event(self, event, payload):
            self.events.append((event, payload))

    app = FakeApp()

    def fail_gateway_construction(*args, **kwargs):
        raise RuntimeError("gateway construction failed")

    monkeypatch.setattr(entry, "create_app", lambda **kwargs: app)
    monkeypatch.setattr(entry, "OperatorGatewayRuntime", fail_gateway_construction)
    monkeypatch.setattr(entry, "NdjsonRpcEndpoint", FakeEndpoint)

    exit_code = await entry.async_main(["--config-json", "{}"])

    assert exit_code == 1
    assert FakeEndpoint.instance.events == [
        (
            "operator.error",
            {
                "message": "gateway construction failed",
                "source": "gateway_startup",
            },
        )
    ]
    assert app.closed is True


def test_tui_01_protocol_mismatch_exits_nonzero_with_structured_error(tmp_path):
    request = {
        "id": 1,
        "method": "operator.initialize",
        "params": {
            "protocol_version": 999,
            "build_stamp": "stale-interaction-bundle",
        },
    }
    config = {
        "home": str(tmp_path / "home"),
        "provider": "fake",
        "workspace": str(tmp_path / "workspace"),
    }

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "demiurge.ui_gateway.entry",
            "--config-json",
            json.dumps(config),
        ],
        input=json.dumps(request) + "\n",
        text=True,
        capture_output=True,
        check=False,
    )
    frames = [json.loads(line) for line in completed.stdout.splitlines() if line.strip()]

    assert completed.returncode == 2
    assert frames == [
        {
            "id": 1,
            "error": {
                "code": "protocol_mismatch",
                "message": "TUI protocol mismatch: expected 1, got 999",
            },
        }
    ]


def test_tui_01_long_command_first_frame_cannot_bypass_identity_gate(tmp_path):
    request = {
        "id": 1,
        "method": "operator.command",
        "params": {"text": "/doctor"},
    }
    config = {
        "home": str(tmp_path / "home"),
        "provider": "fake",
        "workspace": str(tmp_path / "workspace"),
    }

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "demiurge.ui_gateway.entry",
            "--config-json",
            json.dumps(config),
        ],
        input=json.dumps(request) + "\n",
        text=True,
        capture_output=True,
        check=False,
    )
    frames = [json.loads(line) for line in completed.stdout.splitlines() if line.strip()]

    assert completed.returncode == 2
    assert frames == [
        {
            "id": 1,
            "error": {
                "code": "protocol_mismatch",
                "message": "TUI identity handshake required before method: operator.command",
            },
        }
    ]


def test_tui_01_python_and_typescript_protocol_identity_match():
    from demiurge.ui_gateway.protocol import TUI_BUILD_STAMP, TUI_PROTOCOL_VERSION

    source = (
        Path(__file__).resolve().parents[2]
        / "ui-tui"
        / "src"
        / "gateway"
        / "protocol.ts"
    ).read_text(encoding="utf-8")

    assert f"TUI_PROTOCOL_VERSION = {TUI_PROTOCOL_VERSION}" in source
    assert f'TUI_BUILD_STAMP = "{TUI_BUILD_STAMP}"' in source


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


class ControlledRuntime:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.inbounds = []

    async def handle(self, inbound, *, route_binding):
        self.inbounds.append(inbound)
        if inbound.text == "first":
            self.started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled.set()
                raise
        return InteractionOutbound(
            channel="tui",
            session_id=self.session_id,
            items=[
                InteractionItem.delivery_item(
                    InteractionDelivery(type="text", text=f"[next] {inbound.text}"),
                )
            ],
        )


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


def test_ui_01_gateway_startup_error_emits_operator_error_and_exits_nonzero(tmp_path):
    """UI-01: startup errors are observable frames and failing process exits."""
    config = {
        "home": str(tmp_path / "home"),
        "agents_root": str(tmp_path / "missing-agents"),
        "provider": "fake",
    }

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "demiurge.ui_gateway.entry",
            "--config-json",
            json.dumps(config),
        ],
        input="",
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    frames = [json.loads(line) for line in completed.stdout.splitlines() if line]
    startup_error = next(frame for frame in frames if frame.get("event") == "operator.error")
    assert startup_error["payload"]["source"] == "gateway_startup"
    assert startup_error["payload"]["message"]
    assert completed.returncode == 1


def test_ui_01_gateway_configuration_error_is_structured_and_exits_two():
    """UI-01: malformed launcher configuration is a structured usage failure."""
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "demiurge.ui_gateway.entry",
            "--config-json",
            "[",
        ],
        input="",
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    frames = [json.loads(line) for line in completed.stdout.splitlines() if line]
    assert completed.returncode == 2
    assert frames == [
        {
            "event": "operator.error",
            "payload": {
                "code": "config_error",
                "message": "gateway configuration could not be loaded",
                "source": "gateway_config",
            },
        }
    ]
    assert completed.stderr == ""


@pytest.mark.parametrize(
    ("method", "params"),
    [
        pytest.param("operator.shutdown", {}, id="rpc"),
        pytest.param("operator.command", {"text": "/exit"}, id="slash-exit"),
        pytest.param("operator.command", {"text": "/quit"}, id="slash-quit"),
    ],
)
def test_ui_01_gateway_explicit_shutdown_exits_zero(tmp_path, method, params):
    """UI-01: verified explicit shutdown paths are normal zero-exit lifecycles."""
    from demiurge.ui_gateway.protocol import TUI_BUILD_STAMP, TUI_PROTOCOL_VERSION

    config = {
        "home": str(tmp_path / "home"),
        "provider": "fake",
        "workspace": str(tmp_path / "workspace"),
    }
    requests = [
        {
            "id": 1,
            "method": "operator.initialize",
            "params": {
                "protocol_version": TUI_PROTOCOL_VERSION,
                "build_stamp": TUI_BUILD_STAMP,
            },
        },
        {"id": 2, "method": method, "params": params},
    ]

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "demiurge.ui_gateway.entry",
            "--config-json",
            json.dumps(config),
        ],
        input="".join(json.dumps(request) + "\n" for request in requests),
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    frames = [json.loads(line) for line in completed.stdout.splitlines() if line]
    assert completed.returncode == 0
    assert any(frame.get("event") == "operator.shutdown" for frame in frames)


def test_ui_01_gateway_eof_without_shutdown_exits_one(tmp_path):
    """UI-01: losing the client without shutdown is not a normal lifecycle."""
    from demiurge.ui_gateway.protocol import TUI_BUILD_STAMP, TUI_PROTOCOL_VERSION

    config = {
        "home": str(tmp_path / "home"),
        "provider": "fake",
        "workspace": str(tmp_path / "workspace"),
    }
    initialize = {
        "id": 1,
        "method": "operator.initialize",
        "params": {
            "protocol_version": TUI_PROTOCOL_VERSION,
            "build_stamp": TUI_BUILD_STAMP,
        },
    }

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "demiurge.ui_gateway.entry",
            "--config-json",
            json.dumps(config),
        ],
        input=json.dumps(initialize) + "\n",
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert completed.returncode == 1


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


def test_operator_gateway_long_command_isolation_predicate():
    assert _is_long_operator_request("operator.command", {"text": "/evolve improve this"})
    assert _is_long_operator_request("operator.command", {"text": "/packages install memory_basic"})
    assert _is_long_operator_request("operator.command", {"text": "/doctor"})
    assert not _is_long_operator_request("operator.command", {"text": "/exit"})
    assert not _is_long_operator_request("operator.submit", {"text": "/evolve improve this"})


@pytest.mark.asyncio
async def test_tui_bridge_submit_uses_interaction_runtime(tmp_path):
    sink = EventSink()
    app = create_app(home=tmp_path / "home", provider_name="fake")
    bridge = OperatorGatewayRuntime(app, emit=sink)

    await bridge.initialize()
    await bridge.submit("hello")
    await bridge.wait_for_idle()

    assert ("operator.message", {"role": "user", "text": "hello"}) in sink.items
    assert "[fake] hello" in sink.texts()
    received = next(event for event in app.runner.event_log.tail(30) if event["type"] == "message.received")
    assert received["channel"] == "tui"
    assert received["source"] == "local"


@pytest.mark.asyncio
async def test_tui_bridge_ready_includes_tui_slash_command_catalog(tmp_path):
    sink = EventSink()
    app = create_app(home=tmp_path / "home", provider_name="fake")
    bridge = OperatorGatewayRuntime(app, emit=sink)

    await bridge.initialize()

    ready = sink.payloads("operator.ready")[-1]
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
    assert sink.payloads("operator.status")[-1]["user_message_align"] == "left"
    assert sink.payloads("operator.status")[-1]["demiurge_theme_color"] == "#ff9afc"
    assert sink.payloads("operator.status")[-1]["user_theme_color"] == "#9cc9ff"


@pytest.mark.asyncio
async def test_tui_bridge_emits_operator_product_events(tmp_path):
    sink = EventSink()
    app = create_app(home=tmp_path / "home", provider_name="fake")
    bridge = OperatorGatewayRuntime(app, emit=sink)

    await bridge.initialize()

    ready = sink.payloads("operator.ready")[-1]
    status = sink.payloads("operator.status")[-1]

    assert isinstance(bridge, OperatorGatewayRuntime)
    assert ready["session"] == {
        "session_id": app.runner.session_id,
        "channel": "operator",
        "source": "tui",
        "conversation_key": f"tui:{app.runner.session_id}",
    }
    assert status["session"] == ready["session"]
    assert status["work"] == []


@pytest.mark.asyncio
async def test_tui_bridge_ready_includes_host_user_message_align(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.yaml").write_text("ui:\n  user_message_align: right\n", encoding="utf-8")
    sink = EventSink()
    app = create_app(home=home, provider_name="fake")
    bridge = OperatorGatewayRuntime(app, emit=sink)

    await bridge.initialize()

    assert sink.payloads("operator.ready")[-1]["user_message_align"] == "right"
    assert sink.payloads("operator.status")[-1]["user_message_align"] == "right"


@pytest.mark.asyncio
async def test_tui_bridge_ready_includes_host_theme_colors(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.yaml").write_text("ui:\n  demiurge_theme_color: fac\n  user_theme_color: '#aabbcc'\n", encoding="utf-8")
    sink = EventSink()
    app = create_app(home=home, provider_name="fake")
    bridge = OperatorGatewayRuntime(app, emit=sink)

    await bridge.initialize()

    assert sink.payloads("operator.ready")[-1]["demiurge_theme_color"] == "#ffaacc"
    assert sink.payloads("operator.ready")[-1]["user_theme_color"] == "#aabbcc"
    assert sink.payloads("operator.status")[-1]["demiurge_theme_color"] == "#ffaacc"
    assert sink.payloads("operator.status")[-1]["user_theme_color"] == "#aabbcc"


@pytest.mark.asyncio
async def test_tui_bridge_uses_host_config_initial_busy_mode(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.yaml").write_text("channel:\n  busy_mode: queue\n", encoding="utf-8")
    sink = EventSink()
    app = create_app(home=home, provider_name="fake")
    bridge = OperatorGatewayRuntime(app, emit=sink)

    await bridge.initialize()

    assert bridge.busy_mode == "queue"
    assert sink.payloads("operator.ready")[-1]["busy_mode"] == "queue"
    assert sink.payloads("operator.status")[-1]["busy_mode"] == "queue"


@pytest.mark.asyncio
async def test_tui_bridge_tool_display_quiet_summary_full(tmp_path):
    quiet_sink = EventSink()
    quiet_app = create_app(home=tmp_path / "quiet", provider_name="fake")
    quiet = OperatorGatewayRuntime(quiet_app, emit=quiet_sink, tool_display="quiet")
    await quiet.submit("please use tools_list")
    await quiet.wait_for_idle()
    assert not any(payload.get("tool_calls") or payload.get("tool_results") for payload in quiet_sink.payloads("operator.deliver"))
    assert "[fake] tool result received" in quiet_sink.texts()

    full_sink = EventSink()
    full_app = create_app(home=tmp_path / "full", provider_name="fake")
    full = OperatorGatewayRuntime(full_app, emit=full_sink, tool_display="full")
    await full.submit("please use tools_list")
    await full.wait_for_idle()
    tool_payloads = [payload for payload in full_sink.payloads("operator.deliver") if payload.get("tool_calls")]
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
    bridge = OperatorGatewayRuntime(app, emit=sink, tool_display="summary")
    record = ToolExecutionRecord(
        call=ToolCall(name="tools_list", arguments={}, id="call_1"),
        result=ToolResult(content="tool summary", display_output="tool summary"),
    )

    await bridge.deliver(
        InteractionOutbound(
            channel="tui",
            session_id=app.runner.session_id,
            items=[
                InteractionItem.delivery_item(InteractionDelivery(type="text", text="first")),
                InteractionItem.tool_result_item(record),
            ],
        )
    )

    payloads = sink.payloads("operator.deliver")
    assert [bool(payload.get("deliveries")) for payload in payloads] == [True, False]
    assert [bool(payload.get("tool_calls")) for payload in payloads] == [False, True]
    assert payloads[0]["deliveries"][0]["text"] == "first"
    assert payloads[1]["tool_calls"][0]["name"] == "tools_list"


@pytest.mark.asyncio
async def test_tui_bridge_emits_tool_lifecycle_payloads(tmp_path):
    sink = EventSink()
    app = create_app(home=tmp_path / "home", provider_name="fake")
    bridge = OperatorGatewayRuntime(app, emit=sink, tool_display="summary")
    call = ToolCall(name="terminal", arguments={"command": "whoami"}, id="call_1")
    result = ToolResult(content="exit_code: 0\nstdout:\nalice\n", display_output="$ whoami\ncwd: .\nexit_code: 0\nstdout:\nalice\n")

    await bridge.deliver(
        InteractionOutbound(
            channel="tui",
            session_id=app.runner.session_id,
            items=[
                InteractionItem.tool_call_item(ToolInteractionRecord.started(call)),
                InteractionItem.tool_call_item(ToolInteractionRecord.finished(ToolExecutionRecord(call=call, result=result))),
            ],
        )
    )

    payloads = [payload for payload in sink.payloads("operator.deliver") if payload.get("tool_calls")]
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
    bridge = OperatorGatewayRuntime(app, emit=sink)

    await bridge.submit("ask me")
    await bridge.wait_for_idle()

    prompt = sink.payloads("operator.prompt.opened")[-1]
    assert prompt["question"] == "Which path?"
    assert prompt["choices"] == ["fast", "careful"]

    await bridge.reply_prompt(prompt["prompt_id"], "2")
    await bridge.wait_for_idle()

    assert ("operator.message", {"role": "user", "text": "careful"}) in sink.items
    assert "[fake] careful" in sink.texts()


@pytest.mark.asyncio
async def test_tui_bridge_prompt_user_waits_for_reply(tmp_path):
    sink = EventSink()
    app = create_app(home=tmp_path / "home", provider_name="fake")
    bridge = OperatorGatewayRuntime(app, emit=sink)

    task = asyncio.create_task(
        bridge.prompt_user(UserPromptRequest(question="Pick one", choices=["a", "b"], metadata={"kind": "clarify"}))
    )
    await _wait_for(lambda: bool(sink.payloads("operator.prompt.opened")))
    prompt = sink.payloads("operator.prompt.opened")[-1]
    await bridge.reply_prompt(prompt["prompt_id"], "2")

    assert await task == "b"
    assert sink.payloads("operator.prompt.opened")[-1]["prompt_id"] == prompt["prompt_id"]


@pytest.mark.asyncio
async def test_tui_bridge_approval_request_round_trip(tmp_path):
    sink = EventSink()
    app = create_app(home=tmp_path / "home", provider_name="fake")
    bridge = OperatorGatewayRuntime(app, emit=sink)
    core = await app.runner.load_active_core()
    request = ApprovalRequest(
        scope=ApprovalScope.for_host_operation(
            principal_scope=app.runner.principal_scope,
            turn_id="turn_1",
            core_id=core.core_id,
            core_revision=core.revision,
            capability_snapshot=CapabilitySnapshot.capture(core),
        ),
        tool_name="terminal",
        tool_call_id="call_1",
        capability="terminal.exec",
        action="exec",
        risk="critical",
        summary="Run command",
        command="printf hello",
    )

    task = asyncio.create_task(bridge.request_approval(request))
    await _wait_for(lambda: bool(sink.payloads("operator.approval.opened")))
    payload = sink.payloads("operator.approval.opened")[-1]
    assert payload["request"]["tool_name"] == "terminal"
    assert sink.payloads("operator.approval.opened")[-1] == payload

    await bridge.reply_approval(payload["approval_id"], "2")

    decision = await task
    assert decision.value == "always_allow_for_session"


@pytest.mark.asyncio
async def test_tui_bridge_commands_stay_python_side(tmp_path):
    sink = EventSink()
    app = create_app(home=tmp_path / "home", provider_name="fake")
    bridge = OperatorGatewayRuntime(app, emit=sink)

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
    bridge = OperatorGatewayRuntime(app, emit=sink)
    original_session = app.runner.session_id

    await bridge.submit("hello")
    await bridge.wait_for_idle()
    await bridge.command("/new")
    assert app.runner.session_id != original_session
    assert sink.payloads("operator.history")[-1]["items"] == []
    await bridge.command("/resume")
    prompt = sink.payloads("operator.prompt.opened")[-1]
    assert prompt["kind"] == "resume"
    original_index = next(index for index, record in enumerate(prompt["records"], start=1) if record["session_id"] == original_session)

    await bridge.reply_prompt(prompt["prompt_id"], str(original_index))

    assert app.runner.session_id == original_session
    history = sink.payloads("operator.history")[-1]
    assert history["session_id"] == original_session
    assert {"type": "message", "role": "user", "text": "hello"}.items() <= history["items"][0].items()
    assert any(item["type"] == "message" and item["role"] == "assistant" and "[fake] hello" in item["text"] for item in history["items"])
    assert f"resumed session: {original_session}" in sink.texts()


@pytest.mark.asyncio
async def test_tui_bridge_initialize_emits_existing_session_history(tmp_path):
    sink = EventSink()
    app = create_app(home=tmp_path / "home", provider_name="fake")
    bridge = OperatorGatewayRuntime(app, emit=sink)

    await bridge.submit("hello")
    await bridge.wait_for_idle()

    fresh_sink = EventSink()
    fresh_bridge = OperatorGatewayRuntime(app, emit=fresh_sink)
    await fresh_bridge.initialize()

    history = fresh_sink.payloads("operator.history")[-1]
    assert history["session_id"] == app.runner.session_id
    assert any(item["type"] == "message" and item["role"] == "user" and item["text"] == "hello" for item in history["items"])


@pytest.mark.asyncio
async def test_tui_bridge_resume_history_includes_tool_cards(tmp_path):
    sink = EventSink()
    app = create_app(home=tmp_path / "home", provider_name="fake")
    bridge = OperatorGatewayRuntime(app, emit=sink, tool_display="full")
    original_session = app.runner.session_id

    await bridge.submit("please use tools_list")
    await bridge.wait_for_idle()
    await bridge.command("/new")
    await bridge.command(f"/resume {original_session}")

    history = sink.payloads("operator.history")[-1]
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
    bridge = OperatorGatewayRuntime(app, emit=sink, tool_display="quiet")
    original_session = app.runner.session_id

    await bridge.submit("please use tools_list")
    await bridge.wait_for_idle()
    await bridge.command("/new")
    await bridge.command(f"/resume {original_session}")

    history = sink.payloads("operator.history")[-1]
    assert any(item["type"] == "message" and item["role"] == "user" for item in history["items"])
    assert not any(item["type"] == "tool" for item in history["items"])


@pytest.mark.asyncio
async def test_tui_bridge_renders_progress_and_background_delivery(tmp_path):
    sink = EventSink()
    app = create_app(home=tmp_path / "home", provider_name="fake")
    bridge = OperatorGatewayRuntime(app, emit=sink)

    outbound = InteractionOutbound(
        channel="tui",
        session_id=app.runner.session_id,
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

    payload = sink.payloads("operator.deliver")[-1]
    assert payload["deliveries"][0]["kind"] == "progress"
    assert payload["deliveries"][0]["metadata"]["delivery"] == "immediate"
    assert payload["deliveries"][1]["metadata"]["background"] is True


@pytest.mark.asyncio
async def test_tui_bridge_runs_background_completion_turn_when_idle(tmp_path):
    sink = EventSink()
    app = create_app(home=tmp_path / "home", provider_name="fake")
    provider = RecordingEchoProvider()
    app.runner.provider = provider
    bridge = OperatorGatewayRuntime(app, emit=sink)

    await bridge.initialize()
    record = await _complete_background_task(app)

    await _wait_for(lambda: len(provider.requests) == 1 and not bridge.running)

    user_text = next(message.content for message in provider.requests[0].messages if message.role == "user")
    assert "[SYSTEM: Background task event]" in user_text
    assert record.task_id in user_text
    assert "background complete" in user_text
    assert sink.payloads("operator.message") == []


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
    bridge = OperatorGatewayRuntime(app, emit=sink)

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
    bridge = OperatorGatewayRuntime(app, emit=sink)

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
    bridge = OperatorGatewayRuntime(app, emit=sink, busy_mode="queue")

    await bridge.submit("first")
    await provider.started.wait()
    await bridge.submit("second")

    assert provider.cancelled.is_set() is False

    provider.release.set()
    await _wait_for(
        lambda: len(provider.requests) == 2 and "[next] second" in sink.texts() and not bridge.running,
        timeout=30.0,
    )

    output = sink.texts()
    assert "[slow] first" in output
    assert "[next] second" in output


@pytest.mark.asyncio
@pytest.mark.cross_platform
async def test_tui_bridge_interrupt_mode_state_machine_cancels_and_drains_next_input(tmp_path):
    sink = EventSink()
    app = create_app(home=tmp_path / "home", provider_name="fake")
    bridge = OperatorGatewayRuntime(app, emit=sink)
    runtime = ControlledRuntime(app.runner.session_id)
    bridge.runtime = runtime

    assert await bridge.submit("first") == {"accepted": True, "queued": False}
    await runtime.started.wait()

    assert await bridge.submit("second") == {"accepted": True, "queued": True}
    await runtime.cancelled.wait()
    await _wait_for(
        lambda: len(runtime.inbounds) == 2
        and bridge._queued_inputs.empty()
        and not bridge.running
        and "[next] second" in sink.texts(),
        timeout=5.0,
    )

    assert [inbound.text for inbound in runtime.inbounds] == ["first", "second"]
    output = sink.texts()
    assert "interrupting current turn: new input" in output
    assert "turn interrupted" in output
    assert "[next] second" in output


@pytest.mark.asyncio
@pytest.mark.slow_integration
async def test_tui_bridge_interrupt_mode_cancels_current_turn_and_runs_next(tmp_path):
    sink = EventSink()
    app = create_app(home=tmp_path / "home", provider_name="fake")
    provider = BlockingProvider()
    app.runner.provider = provider
    bridge = OperatorGatewayRuntime(app, emit=sink)

    await bridge.submit("first")
    await provider.started.wait()
    await bridge.submit("second")
    await provider.cancelled.wait()
    await _wait_for(lambda: "[next] second" in sink.texts() and not bridge.running, timeout=30.0)

    output = sink.texts()
    assert "interrupting current turn: new input" in output
    assert "turn interrupted" in output
    assert "[next] second" in output
