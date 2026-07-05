import asyncio

import pytest

from demiurge.runtime.ingress import InboundQueueRuntime
from demiurge.runtime.interactions import InteractionInbound


def _user(text: str = "user", *, source: str = "source") -> InteractionInbound:
    return InteractionInbound(channel="test", text=text, source=source, conversation_key="conversation")


def _completion(task_id: str, text: str | None = None) -> InteractionInbound:
    return InteractionInbound(
        channel="test",
        text=text or f"completion {task_id}",
        source="source",
        conversation_key="conversation",
        metadata={
            "trigger": "background_task",
            "task_id": task_id,
            "event_id": f"event_{task_id}",
            "completion_claim_id": f"claim_{task_id}",
        },
    )


@pytest.mark.asyncio
async def test_next_inbound_prefers_user_and_merges_queued_completions():
    queue = InboundQueueRuntime()
    await queue.put(_completion("1"))
    await queue.put(_user("hello"))
    await queue.put(_completion("2"))
    await queue.put(_user("later"))

    selected = queue.next_inbound()

    assert selected.text.startswith("hello")
    assert "completion 1" in selected.text
    assert "completion 2" in selected.text
    assert selected.metadata["merged_background_tasks"] == ["1", "2"]
    assert selected.metadata["completion_claims"] == [
        {"event_id": "event_1", "claim_id": "claim_1"},
        {"event_id": "event_2", "claim_id": "claim_2"},
    ]
    assert queue.qsize() == 1
    assert queue.next_inbound().text == "later"


@pytest.mark.asyncio
async def test_next_inbound_returns_completion_when_no_user_input_exists():
    queue = InboundQueueRuntime()
    completion = _completion("1")
    await queue.put(completion)

    assert queue.next_inbound() is completion
    assert queue.empty()


@pytest.mark.asyncio
async def test_clear_can_preserve_background_completions():
    queue = InboundQueueRuntime()
    await queue.put(_user("first"))
    await queue.put(_completion("1"))
    await queue.put(_user("second"))

    removed = queue.clear(preserve_completions=True)

    assert removed == 2
    assert queue.qsize() == 1
    assert queue.next_inbound().metadata["task_id"] == "1"


def test_next_inbound_raises_queue_empty_when_empty():
    queue = InboundQueueRuntime()

    with pytest.raises(asyncio.QueueEmpty):
        queue.next_inbound()


@pytest.mark.asyncio
async def test_merge_completions_into_preserves_ordinary_queued_inputs():
    queue = InboundQueueRuntime()
    await queue.put(_completion("queued"))
    await queue.put(_user("later"))
    stored = [_completion("stored")]

    selected = queue.merge_completions_into(_user("now"), stored_completions=stored)

    assert selected.text.startswith("now")
    assert "completion stored" in selected.text
    assert "completion queued" in selected.text
    assert selected.metadata["merged_background_tasks"] == ["stored", "queued"]
    assert queue.qsize() == 1
    assert queue.next_inbound().text == "later"
