from dataclasses import dataclass, field
from typing import Any

from demiurge.providers import ToolCall
from demiurge.runtime.interactions import ToolInteractionRecord
from demiurge.runtime.tool_display import (
    historical_tool_item,
    normalize_tool_display,
    tool_call_item,
    tool_call_markdown,
    tool_call_start_summary,
    tool_result_text,
    tool_results_markdown,
)
from demiurge.sdk import ToolResult
from demiurge.tools.records import ToolExecutionRecord


def test_normalize_tool_display_defaults_invalid_values_to_summary():
    assert normalize_tool_display("quiet") == "quiet"
    assert normalize_tool_display("FULL") == "full"
    assert normalize_tool_display("bad") == "summary"
    assert normalize_tool_display(None) == "summary"


def test_tool_call_start_summary_for_terminal_file_and_generic_calls():
    assert tool_call_start_summary(ToolCall(name="terminal", arguments={"command": "whoami"})) == "$ whoami"
    assert tool_call_start_summary(ToolCall(name="read_file", arguments={"path": "README.md"})) == "read_file: README.md"
    assert tool_call_start_summary(ToolCall(name="write_file", arguments={"file_path": "out.txt"})) == "write_file: out.txt"
    assert tool_call_start_summary(ToolCall(name="search", arguments={"q": "agent", "limit": 3})) == '{"limit": 3, "q": "agent"}'
    assert tool_call_start_summary(ToolCall(name="noop", arguments={})) == "running"


def test_tool_result_text_prefers_display_output():
    result = ToolResult(content="model text", display_output="human text")

    assert tool_result_text(result) == "human text"
    assert tool_result_text(ToolResult(content="model text")) == "model text"


def test_summary_markdown_for_started_and_finished_tool_records():
    call = ToolCall(name="terminal", arguments={"command": "whoami"}, id="call_1")
    started = ToolInteractionRecord.started(call)
    finished = ToolInteractionRecord.finished(
        ToolExecutionRecord(call=call, result=ToolResult(content="alice", display_output="display alice"))
    )

    assert tool_call_markdown(started) == "## Tool call\n`terminal` - `running` - $ whoami"
    assert tool_call_markdown(finished) == "## Tool call\n`terminal` - `ok` - display alice"


def test_full_markdown_includes_model_output_only_when_distinct():
    call = ToolCall(name="terminal", arguments={"command": "whoami"}, id="call_1")
    distinct = ToolExecutionRecord(
        call=call,
        result=ToolResult(content="alice", display_output="display alice", model_output="model sees alice"),
    )
    same = ToolExecutionRecord(
        call=call,
        result=ToolResult(content="alice", display_output="display alice", model_output="alice"),
    )

    full = tool_results_markdown([distinct], mode="full")
    assert "Arguments" in full
    assert '"command": "whoami"' in full
    assert "Result" in full
    assert "model sees alice" in full
    assert "Model output" not in tool_results_markdown([same], mode="full")


def test_tool_call_item_returns_tui_payload_shape():
    call = ToolCall(name="terminal", arguments={"command": "whoami"}, id="call_1")
    record = ToolInteractionRecord.finished(
        ToolExecutionRecord(
            call=call,
            result=ToolResult(content="alice", display_output="$ whoami\nalice", model_output="model sees alice"),
        )
    )

    item = tool_call_item(2, record, full=True)

    assert item == {
        "index": 2,
        "name": "terminal",
        "id": "call_1",
        "phase": "finish",
        "status": "ok",
        "summary": "$ whoami alice",
        "arguments": {"command": "whoami"},
        "result": "$ whoami\nalice",
        "model_output": "model sees alice",
    }


@dataclass
class Message:
    id: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


def test_historical_tool_item_reconstructs_tui_tool_card():
    message = Message(id="message_1", content="fallback", metadata={"tool_call_id": "call_1"})
    events = {
        "call_1": {
            "id": "call_1",
            "name": "tools_list",
            "arguments": {},
            "content": "tools listed",
            "display_output": "tools listed",
            "model_output": "model tools",
            "is_error": False,
        }
    }

    item = historical_tool_item(message, events, full=True)

    assert item == {
        "id": "history_tool_call_1",
        "type": "tool",
        "display": "full",
        "tools": [
            {
                "index": 1,
                "name": "tools_list",
                "id": "call_1",
                "status": "ok",
                "summary": "tools listed",
                "arguments": {},
                "result": "tools listed",
                "model_output": "model tools",
            }
        ],
    }
