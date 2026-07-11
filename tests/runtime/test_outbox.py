import pytest

from demiurge.runtime.control import RuntimeControlPlane
from demiurge.runtime.interactions import InteractionDelivery, InteractionItem, InteractionOutbound, SessionInteractionRouter
from demiurge.runtime.outbox import DeliveryRuntime
from demiurge.runtime.session import SessionRuntime
from demiurge.runtime.store import RuntimeQuery, RuntimeStore
from demiurge.storage import EventLog


class RecordingBridge:
    def __init__(self):
        self.outbounds = []

    async def deliver(self, outbound):
        self.outbounds.append(outbound)
        outbound.mark_delivered()


class FailingBridge(RecordingBridge):
    async def deliver(self, outbound):
        raise RuntimeError("bridge boom")


def _queue_delivery(store: RuntimeStore, delivery_id: str = "delivery_1") -> InteractionItem:
    runtime = SessionRuntime(control_plane=RuntimeControlPlane(store))
    record, _ = runtime.ensure_session(
        "session_1",
        core_id="assistant",
        core_revision="0001",
        channel="tui",
        conversation_key="local",
    )
    runtime.append_delivery_message(
        record.session_id,
        role="assistant",
        content="hello",
        turn_id="turn_1",
        delivery_id=delivery_id,
        channel="tui",
        target={"conversation_key": "local"},
        delivery_payload={"fallback_text": "hello"},
        delivery_idempotency_key=delivery_id,
    )
    return InteractionItem.delivery_item(
        InteractionDelivery(
            type="text",
            text="hello",
            metadata={"delivery_id": delivery_id},
        )
    )


@pytest.mark.asyncio
async def test_delivery_runtime_claims_sends_and_marks_sent_once(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    item = _queue_delivery(store)
    bridge = RecordingBridge()
    router = SessionInteractionRouter()
    router.bind("session_1", bridge)
    runtime = DeliveryRuntime(store=store, event_log=EventLog(tmp_path, "session_2"), router=router)

    await runtime.dispatch_item(
        item,
        session_id="session_1",
        turn_id="turn_1",
        channel="tui",
        metadata={},
    )

    outbox = store.query(RuntimeQuery(table="outbox", where={"delivery_id": "delivery_1"})).rows[0]
    work = store.query(RuntimeQuery(table="runtime_work_items", where={"work_id": "delivery_1"})).rows[0]

    assert len(bridge.outbounds) == 1
    assert item.dispatch_status == "delivered"
    assert outbox["status"] == "sent"
    assert outbox["attempts"] == 1
    assert work["status"] == "succeeded"
    assert work["attempts"] == 1


@pytest.mark.asyncio
async def test_delivery_runtime_marks_failed_with_current_claim_attempt(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    item = _queue_delivery(store)
    router = SessionInteractionRouter()
    router.bind("session_1", FailingBridge())
    runtime = DeliveryRuntime(store=store, event_log=EventLog(tmp_path, "session_1"), router=router)

    await runtime.dispatch_item(
        item,
        session_id="session_1",
        turn_id="turn_1",
        channel="tui",
        metadata={},
    )

    outbox = store.query(RuntimeQuery(table="outbox", where={"delivery_id": "delivery_1"})).rows[0]
    work = store.query(RuntimeQuery(table="runtime_work_items", where={"work_id": "delivery_1"})).rows[0]

    assert item.dispatch_status == "failed"
    assert outbox["status"] == "failed"
    assert outbox["attempts"] == 1
    assert outbox["last_error"] == "bridge boom"
    assert work["status"] == "failed"
    assert work["attempts"] == 1
    assert [event["type"] for event in EventLog(tmp_path, "session_1").read_all()] == ["delivery.failed"]
    assert EventLog(tmp_path, "session_2").read_all() == []


@pytest.mark.asyncio
async def test_delivery_runtime_marks_unrouted_without_active_route(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    item = _queue_delivery(store)
    runtime = DeliveryRuntime(
        store=store,
        event_log=EventLog(tmp_path, "session_2"),
        router=SessionInteractionRouter(),
    )

    await runtime.dispatch_item(
        item,
        session_id="session_1",
        turn_id="turn_1",
        channel="tui",
        metadata={},
    )

    outbox = store.query(RuntimeQuery(table="outbox", where={"delivery_id": "delivery_1"})).rows[0]
    work = store.query(RuntimeQuery(table="runtime_work_items", where={"work_id": "delivery_1"})).rows[0]

    assert item.dispatch_status == "unrouted"
    assert outbox["status"] == "unrouted"
    assert outbox["last_error"] == "no_interactive_route"
    assert work["status"] == "succeeded"
    assert [event["type"] for event in EventLog(tmp_path, "session_1").read_all()] == ["delivery.unrouted"]
    assert EventLog(tmp_path, "session_2").read_all() == []


@pytest.mark.asyncio
async def test_bound_route_rejects_mismatched_session_id():
    router = SessionInteractionRouter()
    router.bind("session_1", RecordingBridge())
    bound = router.route_for("session_1")
    assert bound is not None
    outbound = InteractionOutbound(
        "tui",
        session_id="session_2",
        items=[InteractionItem.delivery_item(InteractionDelivery(text="wrong"))],
    )

    with pytest.raises(RuntimeError, match="received outbound for `session_2`"):
        await bound.deliver(outbound)
