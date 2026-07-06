from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from demiurge.core import SlotDefinition
from demiurge.runtime.control import RuntimeControlPlane
from demiurge.runtime.delivery import ContentBlock, DeliveryRequest
from demiurge.runtime.module_delivery import ModuleDeliveryRuntime
from demiurge.runtime.session import SessionRuntime
from demiurge.runtime.store import RuntimeEvent, RuntimeQuery, RuntimeStore
from demiurge.sdk import AgentInput, TurnContext


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


class _Host:
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

    def emit_event(self, event_type: str, **payload: Any) -> dict[str, Any]:
        self.events.append((event_type, dict(payload)))
        return {"type": event_type, **payload}

    def append_runtime_event(self, event: RuntimeEvent) -> None:
        self.control.record_events([event])

    def append_runtime_events(self, events: list[RuntimeEvent]) -> None:
        self.control.record_events(events)

    def rows(self, table: str, *, order_by: str | None = None, **where: Any) -> tuple[dict[str, Any], ...]:
        return self.store.query(RuntimeQuery(table=table, where=where or None, order_by=order_by)).rows


def test_persisted_text_delivery_writes_history_outbox_and_visible_delivery(tmp_path):
    host = _Host(tmp_path)
    runtime = ModuleDeliveryRuntime(host)

    delivery = runtime.apply_request(
        DeliveryRequest(
            delivery_id="delivery_text",
            blocks=[ContentBlock(type="text", text="hello")],
        ),
        turn=_turn(),
        slot=_slot(tmp_path),
        interaction_metadata={"channel": "tui", "conversation_key": "local"},
    )

    messages = host.session_runtime.read_messages("session_1")
    assert [(message.role, message.content, message.model_visible) for message in messages] == [
        ("assistant", "hello", True)
    ]
    outbox = host.rows("outbox", delivery_id="delivery_text")[0]
    work = host.rows("runtime_work_items", work_id="delivery_text")[0]
    assert outbox["payload"]["fallback_text"] == "hello"
    assert outbox["payload"]["message_id"] == messages[0].id
    assert outbox["owner_turn_id"] == "turn_1"
    assert work["kind"] == "delivery.send"
    assert work["owner_turn_id"] == "turn_1"
    assert work["parent_work_id"] is None
    assert work["payload"]["owner_turn_id"] == "turn_1"
    assert delivery is not None
    assert delivery.text == "hello"
    assert delivery.metadata["message_id"] == messages[0].id
    assert [event[0] for event in host.events] == ["message.persisted", "delivery.completed"]


def test_transient_delivery_enqueues_outbox_without_assistant_history(tmp_path):
    host = _Host(tmp_path)
    runtime = ModuleDeliveryRuntime(host)

    delivery = runtime.apply_request(
        DeliveryRequest(
            delivery_id="delivery_transient",
            blocks=[ContentBlock(type="text", text="progress")],
            kind="progress",
            history_policy="transient",
        ),
        turn=_turn(),
        slot=_slot(tmp_path),
        interaction_metadata={"channel": "tui"},
    )

    assert host.session_runtime.read_messages("session_1") == []
    outbox = host.rows("outbox", delivery_id="delivery_transient")[0]
    work = host.rows("runtime_work_items", work_id="delivery_transient")[0]
    assert outbox["payload"]["history_policy"] == "transient"
    assert outbox["payload"]["message_id"] is None
    assert outbox["owner_turn_id"] == "turn_1"
    assert work["status"] == "queued"
    assert work["owner_turn_id"] == "turn_1"
    assert work["parent_work_id"] is None
    assert work["payload"]["owner_turn_id"] == "turn_1"
    assert delivery is not None
    assert delivery.kind == "progress"
    assert delivery.history_policy == "transient"
    assert [event[0] for event in host.events] == ["delivery.completed"]


def test_non_text_delivery_requires_history_text_when_history_is_writable(tmp_path):
    host = _Host(tmp_path)
    runtime = ModuleDeliveryRuntime(host)

    with pytest.raises(ValueError, match="non-text send_\\* with write_history=True requires history_text"):
        runtime.apply_request(
            DeliveryRequest(
                delivery_id="delivery_image",
                blocks=[
                    ContentBlock(
                        type="image",
                        text="image caption",
                        artifact={"kind": "image", "url": "https://example.com/image.png"},
                    )
                ],
            ),
            turn=_turn(),
            slot=_slot(tmp_path),
            interaction_metadata={"channel": "tui"},
        )


def test_artifact_delivery_stores_artifact_and_records_tui_text_fallback_degradation(tmp_path):
    host = _Host(tmp_path)
    runtime = ModuleDeliveryRuntime(host)

    delivery = runtime.apply_request(
        DeliveryRequest(
            delivery_id="delivery_artifact",
            blocks=[
                ContentBlock(
                    type="audio",
                    text="voice ready",
                    artifact={
                        "kind": "audio",
                        "content": "AUDIO",
                        "filename": "voice.txt",
                        "media_type": "audio/plain",
                        "summary": "sample voice",
                    },
                )
            ],
            history_policy="model_hidden",
            history_text="voice result ready",
        ),
        turn=_turn(),
        slot=_slot(tmp_path),
        interaction_metadata={"channel": "tui"},
    )

    messages = host.session_runtime.read_messages("session_1")
    assert [(message.content, message.model_visible) for message in messages] == [("voice result ready", False)]
    artifact_events = host.rows("runtime_events", type="artifact.stored")
    artifact_rows = host.rows("artifacts")
    assert len(artifact_events) == 1
    assert artifact_events[0]["payload"]["kind"] == "audio"
    assert artifact_events[0]["payload"]["owner_turn_id"] == "turn_1"
    assert artifact_rows[0]["owner_turn_id"] == "turn_1"
    assert artifact_rows[0]["kind"] == "audio"
    assert delivery is not None
    assert delivery.blocks[0]["type"] == "audio"
    assert delivery.blocks[0]["artifact"]["resolved_path"].endswith("/voice.txt")
    assert delivery.metadata["artifacts"][0]["summary"] == "sample voice"
    degraded = [event for event in host.events if event[0] == "delivery.degraded"]
    assert [event[1]["reason"] for event in degraded] == ["channel_text_fallback"]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"history_policy": "forever"}, "invalid history_policy"),
        ({"delivery": "later"}, "invalid delivery mode"),
        ({"kind": "toast"}, "invalid delivery kind"),
        ({"target": "other"}, "unsupported delivery target"),
    ],
)
def test_invalid_delivery_request_fields_raise(tmp_path, kwargs, message):
    host = _Host(tmp_path)
    runtime = ModuleDeliveryRuntime(host)

    with pytest.raises(ValueError, match=message):
        runtime.apply_request(
            DeliveryRequest(
                delivery_id="delivery_bad",
                blocks=[ContentBlock(type="text", text="hello")],
                **kwargs,
            ),
            turn=_turn(),
            slot=_slot(tmp_path),
            interaction_metadata={"channel": "tui"},
        )
