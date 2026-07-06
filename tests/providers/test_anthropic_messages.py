from __future__ import annotations

from typing import Any

import pytest

from demiurge.providers import (
    AnthropicMessagesProvider,
    AnthropicMessagesTransport,
    LLMMessage,
    LLMRequest,
    ToolCall,
    ToolDefinition,
)


def test_anthropic_messages_transport_builds_native_payload():
    transport = AnthropicMessagesTransport()
    request = LLMRequest(
        model="claude-test",
        messages=[
            LLMMessage(role="system", content="Be concise."),
            LLMMessage(role="system", content="Use tools when needed."),
            LLMMessage(role="user", content="Read README."),
            LLMMessage(
                role="assistant",
                content="I will inspect it.",
                tool_calls=[ToolCall(id="toolu_1", name="read_file", arguments={"path": "README.md"})],
            ),
            LLMMessage(role="tool", name="read_file", tool_call_id="toolu_1", content="# Demiurge"),
        ],
        tools=[
            ToolDefinition(
                name="read_file",
                description="Read a file",
                input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
            )
        ],
        metadata={"max_tokens": 2048},
    )

    payload = transport.build_payload(request)

    assert payload == {
        "model": "claude-test",
        "max_tokens": 2048,
        "system": "Be concise.\n\nUse tools when needed.",
        "messages": [
            {"role": "user", "content": "Read README."},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "I will inspect it."},
                    {"type": "tool_use", "id": "toolu_1", "name": "read_file", "input": {"path": "README.md"}},
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_1",
                        "content": "# Demiurge",
                    }
                ],
            },
        ],
        "tools": [
            {
                "name": "read_file",
                "description": "Read a file",
                "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}},
            }
        ],
    }


def test_anthropic_messages_transport_normalizes_text_and_tool_use_blocks():
    transport = AnthropicMessagesTransport()
    response = {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "content": [
            {"type": "text", "text": "I will inspect it."},
            {"type": "tool_use", "id": "toolu_1", "name": "read_file", "input": {"path": "README.md"}},
        ],
        "stop_reason": "tool_use",
    }

    normalized = transport.normalize_response(response)

    assert normalized.content == "I will inspect it."
    assert normalized.raw is response
    assert normalized.tool_calls == [ToolCall(id="toolu_1", name="read_file", arguments={"path": "README.md"})]


@pytest.mark.asyncio
async def test_anthropic_messages_provider_posts_native_payload_and_normalizes_response():
    class RecordingAnthropicProvider(AnthropicMessagesProvider):
        def __init__(self) -> None:
            super().__init__(api_key="test-key", base_url="https://api.anthropic.com/v1")
            self.payloads: list[dict[str, Any]] = []

        async def _post_json(self, payload: dict[str, Any]) -> Any:
            self.payloads.append(payload)
            return {
                "content": [
                    {"type": "tool_use", "id": "toolu_1", "name": "read_file", "input": {"path": "README.md"}}
                ]
            }

    provider = RecordingAnthropicProvider()
    request = LLMRequest(
        model="claude-test",
        messages=[LLMMessage(role="user", content="Read README.")],
        tools=[ToolDefinition(name="read_file", description="Read a file", input_schema={"type": "object"})],
    )

    response = await provider.complete(request)

    assert provider.payloads[0]["model"] == "claude-test"
    assert provider.payloads[0]["messages"] == [{"role": "user", "content": "Read README."}]
    assert provider.payloads[0]["tools"] == [
        {"name": "read_file", "description": "Read a file", "input_schema": {"type": "object"}}
    ]
    assert response.tool_calls == [ToolCall(id="toolu_1", name="read_file", arguments={"path": "README.md"})]
