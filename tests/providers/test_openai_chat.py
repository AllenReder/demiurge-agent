from __future__ import annotations

from demiurge.providers import LLMMessage, LLMRequest, OpenAIChatTransport, ToolCall, ToolDefinition
from demiurge.providers.profiles import get_builtin_provider_profile


def test_openai_chat_transport_serializes_messages_tools_and_tool_results():
    transport = OpenAIChatTransport()
    request = LLMRequest(
        model="gpt-test",
        messages=[
            LLMMessage(role="system", content="Be concise."),
            LLMMessage(role="user", content="List tools."),
            LLMMessage(
                role="assistant",
                content="",
                tool_calls=[ToolCall(id="call_1", name="tools_list", arguments={"limit": 2})],
            ),
            LLMMessage(role="tool", name="tools_list", tool_call_id="call_1", content="[]"),
        ],
        tools=[
            ToolDefinition(
                name="tools_list",
                description="List available tools",
                input_schema={"type": "object", "properties": {"limit": {"type": "integer"}}},
            )
        ],
    )

    payload = transport.build_payload(request)

    assert payload == {
        "model": "gpt-test",
        "messages": [
            {"role": "system", "content": "Be concise."},
            {"role": "user", "content": "List tools."},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "tools_list",
                            "arguments": '{"limit": 2}',
                        },
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "name": "tools_list", "content": "[]"},
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "tools_list",
                    "description": "List available tools",
                    "parameters": {"type": "object", "properties": {"limit": {"type": "integer"}}},
                },
            }
        ],
    }


def test_openai_chat_transport_normalizes_assistant_tool_calls():
    transport = OpenAIChatTransport()
    response = {
        "choices": [
            {
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "tools_list", "arguments": '{"limit": 2}'},
                        }
                    ],
                }
            }
        ]
    }

    normalized = transport.normalize_response(response)

    assert normalized.content == ""
    assert normalized.raw is response
    assert normalized.tool_calls == [ToolCall(id="call_1", name="tools_list", arguments={"limit": 2})]


def test_openai_chat_transport_keeps_malformed_tool_arguments_as_raw_text():
    transport = OpenAIChatTransport()
    response = {
        "choices": [
            {
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "broken", "arguments": "{not-json"},
                        }
                    ],
                }
            }
        ]
    }

    normalized = transport.normalize_response(response)

    assert normalized.tool_calls == [ToolCall(id="call_1", name="broken", arguments={"_raw": "{not-json"})]


def test_openai_chat_transport_adds_deepseek_thinking_for_reasoning_models():
    profile = get_builtin_provider_profile("deepseek")
    transport = OpenAIChatTransport(runtime_profile=profile, base_url=profile.base_url)
    for model in ("deepseek-v4-pro", "deepseek-reasoner"):
        request = LLMRequest(model=model, messages=[LLMMessage(role="user", content="Hi")])

        payload = transport.build_payload(request)

        assert payload["extra_body"] == {"thinking": {"type": "enabled"}}


def test_openai_chat_transport_leaves_deepseek_v3_payload_unchanged():
    profile = get_builtin_provider_profile("deepseek")
    transport = OpenAIChatTransport(runtime_profile=profile, base_url=profile.base_url)
    request = LLMRequest(model="deepseek-chat", messages=[LLMMessage(role="user", content="Hi")])

    payload = transport.build_payload(request)

    assert "extra_body" not in payload


def test_openai_chat_transport_metadata_max_tokens_wins_over_profile_default():
    profile = get_builtin_provider_profile("moonshot")
    transport = OpenAIChatTransport(runtime_profile=profile, base_url=profile.base_url)
    request = LLMRequest(
        model="kimi-k2.7-code",
        messages=[LLMMessage(role="user", content="Hi")],
        metadata={"max_tokens": 2048},
    )

    payload = transport.build_payload(request)

    assert payload["max_tokens"] == 2048
