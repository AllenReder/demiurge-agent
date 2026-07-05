from __future__ import annotations

from demiurge.providers import ToolCall
from demiurge.runtime.interactions import (
    InteractionDelivery,
    InteractionItem,
    InteractionOutbound,
    ToolInteractionRecord,
    UserPromptRequest,
)
from demiurge.runtime.outbound_delivery import text_delivery_steps, ui_delivery_steps
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
