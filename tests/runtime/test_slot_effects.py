from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from demiurge.core import SlotDefinition
from demiurge.providers import ToolCall
from demiurge.runtime.control import RuntimeControlPlane
from demiurge.runtime.delivery import ContentBlock, DeliveryRequest
from demiurge.runtime.interaction_dispatch import InteractionDispatchRuntime
from demiurge.runtime.interactions import InteractionItem
from demiurge.runtime.module_delivery import ModuleDeliveryRuntime
from demiurge.runtime.session import SessionRuntime
from demiurge.runtime.slot_effects import SlotEffectRuntime
from demiurge.runtime.store import RuntimeEvent, RuntimeQuery, RuntimeStore
from demiurge.sdk import AgentInput, ContextContribution, EffectRequest, ToolResult, TurnContext
from demiurge.security.capabilities import CapabilityFacade


def _turn() -> TurnContext:
    return TurnContext(
        session_id="session_1",
        turn_id="turn_1",
        core_id="assistant",
        core_revision="rev_1",
        user_input=AgentInput(content="hello"),
    )


def _slot(tmp_path: Path, *, kind: str = "output", history_policy: str = "persist") -> SlotDefinition:
    root = tmp_path / "slot"
    root.mkdir(exist_ok=True)
    return SlotDefinition(
        kind=kind,
        slot_id="summary",
        path=root,
        relative_path=f"agent/{kind}/summary",
        manifest={},
        history_policy=history_policy,
    )


class _EffectHost:
    def __init__(self, tmp_path: Path):
        self.home = tmp_path / "home"
        self.home.mkdir()
        self.session_id = "session_1"
        self.store = RuntimeStore(tmp_path / "runtime.sqlite3")
        self.control = RuntimeControlPlane(self.store)
        self.session_runtime = SessionRuntime(control_plane=self.control)
        self.session_runtime.ensure_session(
            "session_1",
            core_id="assistant",
            core_revision="rev_1",
            channel="tui",
            conversation_key="local",
        )
        self.session_runtime.start_turn(session_id="session_1", turn_id="turn_1", input_ref="inbound:1")
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.scheduled: list[InteractionItem] = []
        self.flushed: list[InteractionItem] = []

    def emit_event(self, event_type: str, **payload: Any) -> dict[str, Any]:
        self.events.append((event_type, dict(payload)))
        return {"type": event_type, **payload}

    def append_runtime_event(self, event: RuntimeEvent) -> None:
        self.control.record_events([event])

    def append_runtime_events(self, events: list[RuntimeEvent]) -> None:
        self.control.record_events(events)

    def rows(self, table: str, *, order_by: str | None = None, **where: Any) -> tuple[dict[str, Any], ...]:
        return self.store.query(RuntimeQuery(table=table, where=where or None, order_by=order_by)).rows


class _Dispatch:
    def __init__(self, host: _EffectHost):
        self.host = host

    def schedule(self, item: InteractionItem, *, turn: TurnContext, interaction_metadata: dict[str, Any]) -> None:
        item.set_dispatch_status("scheduled")
        self.host.scheduled.append(item)

    async def flush_pending(
        self,
        items: list[InteractionItem],
        *,
        turn: TurnContext,
        interaction_metadata: dict[str, Any],
    ) -> None:
        self.host.flushed.extend(items)

    def mark_pending_failed(self, items: list[InteractionItem], *, reason: str) -> None:
        for item in items:
            item.set_dispatch_status("failed")


def _core(*, capabilities: dict[str, Any] | None = None):
    return SimpleNamespace(
        raw_manifest={"capabilities": capabilities or {"defaults": {}}},
        bootstrap_slots=[],
        input_slots=[],
        output_slots=[],
        tool_slots=[],
    )


def _runtime(tmp_path: Path, *, execute_tool_effect=None):
    host = _EffectHost(tmp_path)
    module_delivery = ModuleDeliveryRuntime(host)
    runtime = SlotEffectRuntime(
        home=host.home,
        session_id=lambda: host.session_id,
        workspace=str(tmp_path),
        module_delivery=module_delivery,
        dispatch=_Dispatch(host),
        execute_tool_effect=execute_tool_effect,
        emit_event=host.emit_event,
    )
    return host, runtime


def test_commit_delivery_request_writes_history_outbox_and_returns_interaction_item(tmp_path):
    host, runtime = _runtime(tmp_path)

    item = runtime.commit_delivery_request(
        DeliveryRequest(
            delivery_id="delivery_text",
            blocks=[ContentBlock(type="text", text="hello")],
        ),
        turn=_turn(),
        slot=_slot(tmp_path),
        interaction_metadata={"channel": "tui", "conversation_key": "local"},
    )

    assert item is not None
    assert item.kind == "delivery"
    assert item.delivery is not None
    assert item.delivery.text == "hello"
    messages = host.session_runtime.read_messages("session_1")
    assert [(message.role, message.content, message.model_visible) for message in messages] == [
        ("assistant", "hello", True)
    ]
    assert host.rows("outbox", delivery_id="delivery_text")[0]["payload"]["fallback_text"] == "hello"


def test_apply_deliver_effect_uses_same_commit_path(tmp_path):
    host, runtime = _runtime(tmp_path)

    delivery = runtime.apply_deliver_effect(
        EffectRequest(
            type="deliver",
            payload={"type": "text", "text": "legacy"},
            history_policy="model_hidden",
        ),
        turn=_turn(),
        slot=_slot(tmp_path),
        interaction_metadata={"channel": "tui"},
    )

    assert delivery is not None
    assert delivery.text == "legacy"
    assert host.session_runtime.read_messages("session_1")[0].model_visible is False


@pytest.mark.asyncio
async def test_handle_effects_converts_append_assistant_message_to_delivery(tmp_path):
    host, runtime = _runtime(tmp_path)

    deliveries = await runtime.handle_effects(
        [
            EffectRequest(
                type="append_assistant_message",
                content="legacy output",
                history_policy="model_hidden",
            )
        ],
        core=_core(),
        turn=_turn(),
        capability=CapabilityFacade(_core()),
        slot=_slot(tmp_path),
        interaction_metadata={"channel": "tui"},
    )

    assert [delivery.text for delivery in deliveries] == ["legacy output"]
    assert host.session_runtime.read_messages("session_1")[0].model_visible is False


@pytest.mark.asyncio
async def test_handle_effects_executes_tool_effect_through_host_adapter(tmp_path):
    calls: list[ToolCall] = []

    async def execute_tool_effect(call, core, turn, capability):
        calls.append(call)
        return ToolResult(content="tool output")

    _, runtime = _runtime(tmp_path, execute_tool_effect=execute_tool_effect)
    core = _core(capabilities={"defaults": {"tool.call:tools_list": True}})

    deliveries = await runtime.handle_effects(
        [{"type": "tool_call", "tool_name": "tools_list", "arguments": {"limit": 1}}],
        core=core,
        turn=_turn(),
        capability=CapabilityFacade(core),
        slot=_slot(tmp_path),
        interaction_metadata={},
    )

    assert [(call.name, call.arguments) for call in calls] == [("tools_list", {"limit": 1})]
    assert [delivery.text for delivery in deliveries] == ["tool output"]


@pytest.mark.asyncio
async def test_handle_effects_records_capability_denied_without_executing_tool(tmp_path):
    called = False

    async def execute_tool_effect(call, core, turn, capability):
        nonlocal called
        called = True
        return ToolResult(content="should not run")

    host, runtime = _runtime(tmp_path, execute_tool_effect=execute_tool_effect)
    core = _core()

    deliveries = await runtime.handle_effects(
        [{"type": "tool_call", "tool_name": "tools_list"}],
        core=core,
        turn=_turn(),
        capability=CapabilityFacade(core),
        slot=_slot(tmp_path),
        interaction_metadata={},
    )

    assert deliveries == []
    assert called is False
    assert host.events == [
        (
            "capability.denied",
            {
                "turn_id": "turn_1",
                "slot": "agent/output/summary",
                "error": (
                    "capability denied: tool.call:tools_list "
                    "for agent/output/summary"
                ),
            },
        )
    ]


def test_normalize_context_items_applies_default_placement(tmp_path):
    _, runtime = _runtime(tmp_path)

    items = runtime.normalize_context_items(
        [
            {"type": "instruction", "content": "from dict"},
            ContextContribution(
                type="instruction",
                content="from object",
                placement="",
            ),
        ],
        default_placement="system_context",
    )

    assert [(item.content, item.placement) for item in items] == [
        ("from dict", "system_context"),
        ("from object", "system_context"),
    ]


def test_module_io_client_send_text_commits_and_schedules_through_effect_runtime(tmp_path):
    host, runtime = _runtime(tmp_path)
    items: list[InteractionItem] = []
    io_client = runtime.module_io_client(
        _slot(tmp_path),
        turn=_turn(),
        interaction_metadata={"channel": "tui"},
        items=items,
    )

    handle = io_client.send_text("from slot")

    assert handle.delivery_id.startswith("delivery_")
    assert len(items) == 1
    assert host.scheduled == items
    assert host.session_runtime.read_messages("session_1")[0].content == "from slot"


@pytest.mark.asyncio
async def test_slot_effect_runtime_can_use_real_interaction_dispatch(tmp_path):
    host, runtime = _runtime(tmp_path)
    dispatched: list[InteractionItem] = []

    class DeliveryRuntime:
        async def dispatch_item(self, item, **kwargs):
            dispatched.append(item)
            item.set_dispatch_status("delivered")

    runtime.dispatch = InteractionDispatchRuntime(
        session_id=lambda: host.session_id,
        delivery_runtime=DeliveryRuntime(),
        track_background_task=lambda task: None,
    )
    item = runtime.commit_delivery_request(
        DeliveryRequest(delivery_id="delivery_text", blocks=[ContentBlock(type="text", text="hello")]),
        turn=_turn(),
        slot=_slot(tmp_path),
        interaction_metadata={"channel": "tui"},
    )
    assert item is not None

    await runtime.flush_background_items([item], turn=_turn(), interaction_metadata={"channel": "tui"})

    assert dispatched == [item]
    assert item.dispatch_status == "delivered"
