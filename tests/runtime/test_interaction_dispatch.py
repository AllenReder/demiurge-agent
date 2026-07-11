import asyncio

import pytest

from demiurge.runtime.interaction_dispatch import InteractionDispatchRuntime
from demiurge.runtime.interactions import InteractionDelivery, InteractionItem
from demiurge.sdk import AgentInput, TurnContext


def _turn() -> TurnContext:
    return TurnContext(
        session_id="session_1",
        turn_id="turn_1",
        core_id="assistant",
        core_revision="rev_1",
        user_input=AgentInput(content="hello"),
    )


def _delivery_item(**metadata) -> InteractionItem:
    return InteractionItem.delivery_item(
        InteractionDelivery(
            type="text",
            text="hello",
            fallback_text="hello",
            metadata={
                "delivery_id": "delivery_1",
                "delivery_status": "pending",
                **metadata,
            },
        )
    )


class _DeliveryRuntime:
    def __init__(self):
        self.calls = []

    async def dispatch_item(self, item, **kwargs):
        self.calls.append((item, kwargs))
        item.set_dispatch_status("delivered")


class _Harness:
    def __init__(self):
        self.delivery_runtime = _DeliveryRuntime()
        self.tasks: list[asyncio.Task] = []
        self.runtime = InteractionDispatchRuntime(
            delivery_runtime=self.delivery_runtime,
            track_background_task=self.tasks.append,
        )


@pytest.mark.asyncio
async def test_schedule_marks_item_scheduled_and_dispatches_in_background():
    harness = _Harness()
    item = _delivery_item()

    harness.runtime.schedule(item, turn=_turn(), interaction_metadata={"channel": "tui"})
    await asyncio.gather(*harness.tasks)

    assert item.dispatch_status == "delivered"
    assert len(harness.delivery_runtime.calls) == 1
    _, kwargs = harness.delivery_runtime.calls[0]
    assert kwargs["session_id"] == "session_1"
    assert kwargs["turn_id"] == "turn_1"
    assert kwargs["channel"] == "tui"
    assert kwargs["metadata"]["delivery_id"] == "delivery_1"
    assert "turn_id" not in kwargs["event_metadata"]


def test_schedule_without_channel_marks_item_unrouted_without_background_task():
    harness = _Harness()
    item = _delivery_item()

    harness.runtime.schedule(item, turn=_turn(), interaction_metadata={})

    assert item.dispatch_status == "unrouted"
    assert harness.tasks == []
    assert harness.delivery_runtime.calls == []


@pytest.mark.asyncio
async def test_flush_pending_dispatches_synchronously_and_preserves_route_metadata():
    harness = _Harness()
    item = _delivery_item(route={"channel": "telegram", "source": "chat_1"})

    await harness.runtime.flush_pending([item], turn=_turn(), interaction_metadata={"channel": "tui"})

    assert item.dispatch_status == "delivered"
    assert harness.tasks == []
    _, kwargs = harness.delivery_runtime.calls[0]
    assert kwargs["channel"] == "telegram"
    assert kwargs["metadata"]["source"] == "chat_1"


def test_mark_pending_failed_annotates_item_and_delivery_metadata():
    harness = _Harness()
    item = _delivery_item()

    harness.runtime.mark_pending_failed([item], reason="slot_failed")

    assert item.dispatch_status == "failed"
    assert item.metadata["delivery_failed_reason"] == "slot_failed"
    assert item.delivery is not None
    assert item.delivery.metadata["delivery_failed_reason"] == "slot_failed"
