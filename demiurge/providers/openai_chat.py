from __future__ import annotations

import json
from typing import Any

from demiurge.providers.profiles import ProviderRuntimeProfile
from demiurge.providers.types import LLMMessage, LLMRequest, LLMResponse, ToolCall, ToolDefinition


class OpenAIChatTransport:
    api_mode = "openai-chat"

    def __init__(
        self,
        *,
        runtime_profile: ProviderRuntimeProfile | None = None,
        base_url: str | None = None,
    ) -> None:
        self.runtime_profile = runtime_profile
        self.base_url = base_url

    def build_payload(self, request: LLMRequest) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": request.model,
            "messages": [self.to_message(message) for message in request.messages],
            "tools": [self.to_tool(tool) for tool in request.tools] or None,
        }
        if self.runtime_profile is None:
            return payload

        request_max_tokens = request.metadata.get("max_tokens") or request.metadata.get("max_output_tokens")
        if _positive_int(request_max_tokens):
            payload["max_tokens"] = request_max_tokens
        elif self.runtime_profile.default_max_tokens:
            payload["max_tokens"] = self.runtime_profile.default_max_tokens

        extras = self.runtime_profile.build_request_extras(request, base_url=self.base_url)
        top_level_kwargs = dict(extras.top_level_kwargs)
        top_level_extra_body = top_level_kwargs.pop("extra_body", None)
        top_level_extra_headers = top_level_kwargs.pop("extra_headers", None)
        if extras.extra_body or isinstance(top_level_extra_body, dict):
            payload["extra_body"] = {
                **dict(payload.get("extra_body") or {}),
                **extras.extra_body,
                **(top_level_extra_body if isinstance(top_level_extra_body, dict) else {}),
            }
        headers = {
            **self.runtime_profile.default_headers,
            **extras.extra_headers,
            **(top_level_extra_headers if isinstance(top_level_extra_headers, dict) else {}),
        }
        if headers:
            payload["extra_headers"] = {**dict(payload.get("extra_headers") or {}), **headers}
        payload.update(top_level_kwargs)
        return payload

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
        runtime_profile: ProviderRuntimeProfile | None = None,
    ):
        self.api_key = api_key
        self.base_url = base_url
        self.runtime_profile = runtime_profile
        self.transport = transport or OpenAIChatTransport(runtime_profile=runtime_profile, base_url=base_url)
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


def _positive_int(value: Any) -> bool:
    return isinstance(value, int) and value > 0
