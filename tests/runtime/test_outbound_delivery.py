from __future__ import annotations

import pytest

from demiurge.providers import ToolCall
from demiurge.runtime.interactions import (
    InteractionDelivery,
    InteractionItem,
    InteractionOutbound,
    ToolInteractionRecord,
    UserPromptRequest,
)
from demiurge.runtime.outbound_delivery import (
    NativeDeliveryRuntime,
    NativeMediaRequest,
    delivery_text_chunks,
    media_block_fallback,
    native_delivery_items,
    text_delivery_steps,
    text_outbound_target,
    ui_delivery_steps,
)
from demiurge.runtime.outbound_delivery import TextOutboundDeliveryRuntime
from demiurge.sdk import ToolResult
from demiurge.tools.records import ToolExecutionRecord


def _outbound(
    *items: InteractionItem,
    prompt: UserPromptRequest | None = None,
    metadata: dict | None = None,
) -> InteractionOutbound:
    return InteractionOutbound(
        "test",
        session_id="session_1",
        turn_id="turn_1",
        items=list(items),
        prompt=prompt,
        metadata=metadata,
    )


def _delivery(text: str) -> InteractionDelivery:
    return InteractionDelivery(text=text, fallback_text=text)


def _tool_call(call_id: str, name: str = "terminal") -> ToolInteractionRecord:
    return ToolInteractionRecord.started(ToolCall(name=name, arguments={"command": "pwd"}, id=call_id))


def _tool_result(call_id: str, content: str) -> ToolExecutionRecord:
    return ToolExecutionRecord(
        call=ToolCall(name="terminal", arguments={"command": "pwd"}, id=call_id),
        result=ToolResult(content=content),
    )


def test_text_delivery_steps_batch_tool_results_until_delivery_or_tool_call():
    first = _tool_result("call_1", "one")
    second = _tool_result("call_2", "two")
    delivery = _delivery("done")
    call = _tool_call("call_3")
    third = _tool_result("call_3", "three")
    prompt = UserPromptRequest("Continue?")

    steps = text_delivery_steps(
        _outbound(
            InteractionItem.tool_result_item(first),
            InteractionItem.tool_result_item(second),
            InteractionItem.delivery_item(delivery),
            InteractionItem.tool_call_item(call),
            InteractionItem.tool_result_item(third),
            prompt=prompt,
        )
    )

    assert [step.kind for step in steps] == ["tool_results", "delivery", "tool_call", "tool_results", "prompt"]
    assert steps[0].tool_results == (first, second)
    assert steps[1].deliveries == (delivery,)
    assert steps[2].tool_call is call
    assert steps[3].tool_results == (third,)
    assert steps[4].prompt is prompt


def test_text_delivery_steps_ignores_items_without_matching_payload():
    steps = text_delivery_steps(
        _outbound(
            InteractionItem(kind="tool_result"),
            InteractionItem(kind="tool_call"),
            InteractionItem(kind="delivery"),
        )
    )

    assert steps == []


def test_text_outbound_target_stringifies_route_metadata():
    outbound = _outbound(metadata={"source": 123, "reply_to": 456, "conversation_key": "chat"})

    target = text_outbound_target(outbound)

    assert target is not None
    assert target.source == "123"
    assert target.reply_to == "456"
    assert target.metadata == {"source": 123, "reply_to": 456, "conversation_key": "chat"}
    assert target.metadata is not outbound.metadata


def test_text_outbound_target_returns_none_without_source():
    assert text_outbound_target(_outbound(metadata={"reply_to": 456})) is None


def test_media_block_fallback_renders_artifact_summary():
    assert (
        media_block_fallback(
            {
                "type": "image",
                "text": "preview",
                "artifact": {"artifact_id": "a1", "kind": "image", "summary": "plot"},
            }
        )
        == "preview\n[artifact:a1 image plot]"
    )


def test_delivery_text_chunks_renders_text_blocks_and_media_fallbacks():
    delivery = InteractionDelivery(
        text="fallback",
        blocks=[
            {"type": "text", "text": "intro"},
            {"type": "image", "artifact": {"artifact_id": "a1", "kind": "image", "summary": "plot"}},
        ],
    )

    assert delivery_text_chunks(delivery) == ["intro", "[artifact:a1 image plot]"]


def test_native_delivery_items_plans_text_only_delivery():
    delivery = InteractionDelivery(text="done", fallback_text="fallback")

    items = native_delivery_items(delivery)

    assert len(items) == 1
    assert items[0].kind == "text"
    assert items[0].text == "done"


def test_native_delivery_items_preserves_mixed_block_order_and_media_request_metadata():
    metadata = {"alt": "plot"}
    delivery = InteractionDelivery(
        text="fallback",
        blocks=[
            {"type": "text", "text": "intro"},
            {
                "type": "image",
                "text": "plot",
                "artifact": {
                    "artifact_id": "a1",
                    "kind": "image",
                    "url": "https://example.com/plot.png",
                    "summary": "plot",
                },
                "metadata": metadata,
            },
        ],
    )

    items = native_delivery_items(delivery)

    assert [item.kind for item in items] == ["text", "media"]
    assert items[0].text == "intro"
    media = items[1].media
    assert media == NativeMediaRequest(
        kind="image",
        source="https://example.com/plot.png",
        caption="plot",
        fallback_text="plot\n[artifact:a1 image plot]",
        metadata={"alt": "plot"},
    )
    assert media is not None
    assert media.metadata is not metadata
    assert items[1].fallback_text == "plot\n[artifact:a1 image plot]"


def test_native_delivery_items_keeps_media_fallback_when_artifact_has_no_source():
    delivery = InteractionDelivery(
        blocks=[
            {
                "type": "file",
                "artifact": {"artifact_id": "a1", "kind": "file", "summary": "report"},
            }
        ],
    )

    items = native_delivery_items(delivery)

    assert len(items) == 1
    assert items[0].kind == "media"
    assert items[0].media is None
    assert items[0].fallback_text == "[artifact:a1 file report]"


async def _noop_prompt(_prompt: UserPromptRequest) -> str:
    return ""


async def _raise_on_delivery(_delivery: InteractionDelivery, *, outbound: InteractionOutbound) -> None:
    raise RuntimeError("delivery failed")


async def _unused_tool_call_delivery(_record: ToolInteractionRecord, *, outbound: InteractionOutbound) -> None:
    raise AssertionError("tool call delivery should not run")


async def _unused_tool_results_delivery(_records: list[ToolExecutionRecord], *, outbound: InteractionOutbound) -> None:
    raise AssertionError("tool results delivery should not run")


def _marked_item_statuses(outbound: InteractionOutbound) -> list[str]:
    return [item.dispatch_status for item in outbound.items]


def _recording_text_delivery_runtime(calls: list[tuple[str, object]]) -> TextOutboundDeliveryRuntime:
    async def deliver_tool_call(record: ToolInteractionRecord, *, outbound: InteractionOutbound) -> None:
        calls.append(("tool_call", record.call.id))

    async def deliver_tool_results(records: list[ToolExecutionRecord], *, outbound: InteractionOutbound) -> None:
        calls.append(("tool_results", [record.call.id for record in records]))

    async def deliver_delivery(delivery: InteractionDelivery, *, outbound: InteractionOutbound) -> None:
        calls.append(("delivery", delivery.text))

    return TextOutboundDeliveryRuntime(
        deliver_tool_call=deliver_tool_call,
        deliver_tool_results=deliver_tool_results,
        deliver_delivery=deliver_delivery,
        prompt_user=_noop_prompt,
    )


def _recording_native_delivery_runtime(
    calls: list[tuple[str, object]],
    *,
    media_success: bool = True,
) -> NativeDeliveryRuntime:
    async def send_text(source: str, text: str, *, reply_to: str | None = None) -> None:
        calls.append(("text", {"source": source, "text": text, "reply_to": reply_to}))

    async def send_media(
        request: NativeMediaRequest,
        *,
        target,
        reply_to: str | None = None,
    ) -> bool:
        calls.append(("media", {"source": target.source, "request": request, "reply_to": reply_to}))
        return media_success

    return NativeDeliveryRuntime(send_text=send_text, send_media=send_media)


@pytest.mark.asyncio
async def test_text_outbound_delivery_runtime_dispatches_text_steps_in_order():
    calls: list[tuple[str, object]] = []
    first = _tool_result("call_1", "one")
    second = _tool_result("call_2", "two")
    call = _tool_call("call_3")
    delivery = _delivery("done")
    outbound = _outbound(
        InteractionItem.tool_result_item(first),
        InteractionItem.tool_result_item(second),
        InteractionItem.tool_call_item(call),
        InteractionItem.delivery_item(delivery),
    )
    runtime = _recording_text_delivery_runtime(calls)

    await runtime.deliver(outbound)

    assert calls == [
        ("tool_results", ["call_1", "call_2"]),
        ("tool_call", "call_3"),
        ("delivery", "done"),
    ]
    assert _marked_item_statuses(outbound) == ["delivered", "delivered", "delivered", "delivered"]


@pytest.mark.asyncio
async def test_text_outbound_delivery_runtime_marks_delivered_after_exception():
    outbound = _outbound(InteractionItem.delivery_item(_delivery("fail")))
    runtime = TextOutboundDeliveryRuntime(
        deliver_tool_call=_unused_tool_call_delivery,
        deliver_tool_results=_unused_tool_results_delivery,
        deliver_delivery=_raise_on_delivery,
        prompt_user=_noop_prompt,
    )

    with pytest.raises(RuntimeError, match="delivery failed"):
        await runtime.deliver(outbound)

    assert _marked_item_statuses(outbound) == ["delivered"]


@pytest.mark.asyncio
async def test_native_delivery_runtime_sends_text_and_media_in_order_with_first_reply_anchor():
    calls: list[tuple[str, object]] = []
    runtime = _recording_native_delivery_runtime(calls)
    target = text_outbound_target(_outbound(metadata={"source": "123", "reply_to": "456"}))
    assert target is not None
    delivery = InteractionDelivery(
        blocks=[
            {"type": "text", "text": "intro"},
            {
                "type": "image",
                "artifact": {
                    "artifact_id": "a1",
                    "kind": "image",
                    "url": "https://example.com/plot.png",
                    "summary": "plot",
                },
            },
        ],
    )

    await runtime.deliver(delivery, target=target)

    assert calls == [
        ("text", {"source": "123", "text": "intro", "reply_to": "456"}),
        (
            "media",
            {
                "source": "123",
                "request": NativeMediaRequest(
                    kind="image",
                    source="https://example.com/plot.png",
                    caption="plot",
                    fallback_text="[artifact:a1 image plot]",
                    metadata={},
                ),
                "reply_to": None,
            },
        ),
    ]


@pytest.mark.asyncio
async def test_native_delivery_runtime_aggregates_failed_media_fallbacks():
    calls: list[tuple[str, object]] = []
    runtime = _recording_native_delivery_runtime(calls, media_success=False)
    target = text_outbound_target(_outbound(metadata={"source": "123", "reply_to": "456"}))
    assert target is not None
    delivery = InteractionDelivery(
        blocks=[
            {
                "type": "image",
                "artifact": {
                    "artifact_id": "a1",
                    "kind": "image",
                    "url": "https://example.com/plot.png",
                    "summary": "plot",
                },
            },
            {
                "type": "file",
                "artifact": {"artifact_id": "a2", "kind": "file", "summary": "report"},
            },
        ],
    )

    await runtime.deliver(delivery, target=target)

    assert calls[-1] == (
        "text",
        {
            "source": "123",
            "text": "[artifact:a1 image plot]\n\n[artifact:a2 file report]",
            "reply_to": "456",
        },
    )


def test_ui_delivery_steps_batches_deliveries_and_projects_tool_results_as_finished_tool_calls():
    first_delivery = _delivery("first")
    call = _tool_call("call_1")
    result = _tool_result("call_1", "ok")
    second_delivery = _delivery("second")
    third_delivery = _delivery("third")

    steps = ui_delivery_steps(
        _outbound(
            InteractionItem.delivery_item(first_delivery),
            InteractionItem.tool_call_item(call),
            InteractionItem.tool_result_item(result),
            InteractionItem.delivery_item(second_delivery),
            InteractionItem.delivery_item(third_delivery),
        )
    )

    assert [step.kind for step in steps] == ["deliveries", "tool_calls", "deliveries"]
    assert steps[0].deliveries == (first_delivery,)
    assert steps[1].tool_calls[0] is call
    assert steps[1].tool_calls[1].phase == "finish"
    assert steps[1].tool_calls[1].result is result.result
    assert steps[2].deliveries == (second_delivery, third_delivery)


def test_ui_delivery_steps_appends_prompt_after_items():
    prompt = UserPromptRequest("Pick one", choices=["a", "b"])

    steps = ui_delivery_steps(_outbound(InteractionItem.delivery_item(_delivery("message")), prompt=prompt))

    assert [step.kind for step in steps] == ["deliveries", "prompt"]
    assert steps[-1].prompt is prompt
