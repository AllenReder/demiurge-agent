from __future__ import annotations

import json
from typing import Any

from demiurge.providers.types import LLMMessage, LLMRequest, LLMResponse, ToolCall, ToolDefinition


class OpenAIChatTransport:
    api_mode = "openai-chat"

    def build_payload(self, request: LLMRequest) -> dict[str, Any]:
        return {
            "model": request.model,
            "messages": [self.to_message(message) for message in request.messages],
            "tools": [self.to_tool(tool) for tool in request.tools] or None,
        }

    def normalize_response(self, response: Any) -> LLMResponse:
        raw = _response_dump(response)
        message = _first_choice_message(response, raw)
        calls: list[ToolCall] = []
        for call in _value(message, "tool_calls", []) or []:
            function = _value(call, "function", {})
            raw_arguments = _value(function, "arguments", "") or "{}"
            try:
                arguments = json.loads(raw_arguments)
            except json.JSONDecodeError:
                arguments = {"_raw": raw_arguments}
            calls.append(
                ToolCall(
                    id=_value(call, "id", "tool_call"),
                    name=_value(function, "name", ""),
                    arguments=arguments,
                )
            )
        return LLMResponse(content=_value(message, "content", "") or "", tool_calls=calls, raw=raw)

    def to_message(self, message: LLMMessage) -> dict[str, Any]:
        if message.role == "assistant" and message.tool_calls:
            return {
                "role": "assistant",
                "content": message.content or None,
                "tool_calls": [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": json.dumps(call.arguments),
                        },
                    }
                    for call in message.tool_calls
                ],
            }
        if message.role == "tool":
            return {
                "role": "tool",
                "tool_call_id": message.tool_call_id,
                "name": message.name,
                "content": message.content,
            }
        return {"role": message.role, "content": message.content}

    def to_tool(self, tool: ToolDefinition) -> dict[str, Any]:
        schema = tool.input_schema or {"type": "object", "properties": {}}
        return {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": schema,
            },
        }


class OpenAIChatProvider:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        transport: OpenAIChatTransport | None = None,
    ):
        self.api_key = api_key
        self.base_url = base_url
        self.transport = transport or OpenAIChatTransport()
        if not self.api_key:
            raise RuntimeError("API key is required for the OpenAI Chat provider")

    async def complete(self, request: LLMRequest) -> LLMResponse:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url)
        response = await client.chat.completions.create(**self.transport.build_payload(request))
        return self.transport.normalize_response(response)


def _response_dump(response: Any) -> Any:
    if isinstance(response, dict):
        return response
    if hasattr(response, "model_dump"):
        return response.model_dump()
    return response


def _first_choice_message(response: Any, raw: Any) -> Any:
    if isinstance(raw, dict):
        return raw["choices"][0]["message"]
    return response.choices[0].message


def _value(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)
