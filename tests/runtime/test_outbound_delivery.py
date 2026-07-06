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
from demiurge.runtime.outbound_delivery import text_delivery_steps, ui_delivery_steps
from demiurge.runtime.outbound_delivery import TextOutboundDeliveryRuntime
from demiurge.sdk import ToolResult
from demiurge.tools.records import ToolExecutionRecord


def _outbound(*items: InteractionItem, prompt: UserPromptRequest | None = None) -> InteractionOutbound:
    return InteractionOutbound("test", session_id="session_1", turn_id="turn_1", items=list(items), prompt=prompt)


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
