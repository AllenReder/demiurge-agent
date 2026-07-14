from pathlib import Path
from typing import Any

import pytest

from demiurge.core import SlotDefinition
from demiurge.runtime.control import RuntimeControlPlane
from demiurge.runtime.delivery import ContentBlock, DeliveryRequest
from demiurge.runtime.interaction_dispatch import InteractionDispatchRuntime
from demiurge.runtime.interactions import InteractionItem
from demiurge.runtime.module_delivery import ModuleDeliveryRuntime
from demiurge.runtime.session import SessionRuntime
from demiurge.runtime.slot_effects import SlotEffectRuntime
from demiurge.runtime.store import RuntimeEvent, RuntimeQuery, RuntimeStore
from demiurge.sdk import AgentInput, ContextContribution, TurnContext


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


def _runtime(tmp_path: Path):
    host = _EffectHost(tmp_path)
    module_delivery = ModuleDeliveryRuntime(host)
    runtime = SlotEffectRuntime(
        home=host.home,
        workspace=str(tmp_path),
        module_delivery=module_delivery,
        dispatch=_Dispatch(host),
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


def test_module_io_client_keeps_captured_turn_session_when_runner_session_changes(tmp_path):
    host, runtime = _runtime(tmp_path)
    host.session_id = "session_2"

    io_client = runtime.module_io_client(
        _slot(tmp_path),
        turn=_turn(),
        interaction_metadata={"channel": "tui"},
    )

    assert io_client.session_id == "session_1"
    assert io_client.route.session_id == "session_1"


@pytest.mark.asyncio
async def test_slot_effect_runtime_can_use_real_interaction_dispatch(tmp_path):
    host, runtime = _runtime(tmp_path)
    dispatched: list[InteractionItem] = []

    class DeliveryRuntime:
        async def dispatch_item(self, item, **kwargs):
            dispatched.append(item)
            item.set_dispatch_status("delivered")

    runtime.dispatch = InteractionDispatchRuntime(
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
