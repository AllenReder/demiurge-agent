from __future__ import annotations

import asyncio
import json
import urllib.request
from typing import Any

from demiurge.providers.profiles import ProviderRuntimeProfile
from demiurge.providers.types import LLMMessage, LLMRequest, LLMResponse, ToolCall, ToolDefinition


class AnthropicMessagesTransport:
    api_mode = "anthropic-messages"
    default_max_tokens = 4096

    def __init__(self, *, runtime_profile: ProviderRuntimeProfile | None = None) -> None:
        self.runtime_profile = runtime_profile

    def build_payload(self, request: LLMRequest) -> dict[str, Any]:
        system, messages = self.convert_messages(request.messages)
        payload: dict[str, Any] = {
            "model": request.model,
            "max_tokens": self._max_tokens(request),
            "messages": messages,
        }
        if system:
            payload["system"] = system
        tools = self.convert_tools(request.tools)
        if tools:
            payload["tools"] = tools
        return payload

    def convert_messages(self, messages: list[LLMMessage]) -> tuple[str | None, list[dict[str, Any]]]:
        system_parts: list[str] = []
        converted: list[dict[str, Any]] = []
        for message in messages:
            if message.role == "system":
                if message.content:
                    system_parts.append(message.content)
                continue
            converted.append(self.to_message(message))
        return "\n\n".join(system_parts) or None, converted

    def convert_tools(self, tools: list[ToolDefinition]) -> list[dict[str, Any]]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema or {"type": "object", "properties": {}},
            }
            for tool in tools
        ]

    def to_message(self, message: LLMMessage) -> dict[str, Any]:
        if message.role == "tool":
            return {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": message.tool_call_id or message.name or "tool_call",
                        "content": message.content,
                    }
                ],
            }
        if message.role == "assistant" and message.tool_calls:
            content: list[dict[str, Any]] = []
            if message.content:
                content.append({"type": "text", "text": message.content})
            for call in message.tool_calls:
                content.append(
                    {
                        "type": "tool_use",
                        "id": call.id,
                        "name": call.name,
                        "input": call.arguments,
                    }
                )
            return {"role": "assistant", "content": content}
        role = "assistant" if message.role == "assistant" else "user"
        return {"role": role, "content": message.content}

    def normalize_response(self, response: Any) -> LLMResponse:
        raw = _response_dump(response)
        content_blocks = _value(raw, "content", []) or []
        text_parts: list[str] = []
        calls: list[ToolCall] = []
        for block in content_blocks:
            block_type = _value(block, "type")
            if block_type == "text":
                text = _value(block, "text", "")
                if text:
                    text_parts.append(text)
            elif block_type == "tool_use":
                tool_input = _value(block, "input", {})
                arguments = tool_input if isinstance(tool_input, dict) else {"_raw": tool_input}
                calls.append(
                    ToolCall(
                        id=_value(block, "id", "tool_call"),
                        name=_value(block, "name", ""),
                        arguments=arguments,
                    )
                )
        return LLMResponse(content="\n".join(text_parts), tool_calls=calls, raw=raw)

    def _max_tokens(self, request: LLMRequest) -> int:
        value = request.metadata.get("max_tokens") or request.metadata.get("max_output_tokens")
        if isinstance(value, int) and value > 0:
            return value
        if self.runtime_profile and self.runtime_profile.default_max_tokens:
            return self.runtime_profile.default_max_tokens
        return self.default_max_tokens


class AnthropicMessagesProvider:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        anthropic_version: str = "2023-06-01",
        transport: AnthropicMessagesTransport | None = None,
        runtime_profile: ProviderRuntimeProfile | None = None,
    ):
        self.api_key = api_key
        self.base_url = (base_url or "https://api.anthropic.com/v1").rstrip("/")
        self.anthropic_version = anthropic_version
        self.runtime_profile = runtime_profile
        self.transport = transport or AnthropicMessagesTransport(runtime_profile=runtime_profile)
        if not self.api_key:
            raise RuntimeError("API key is required for the Anthropic Messages provider")

    async def complete(self, request: LLMRequest) -> LLMResponse:
        payload = self.transport.build_payload(request)
        raw = await self._post_json(payload)
        return self.transport.normalize_response(raw)

    async def _post_json(self, payload: dict[str, Any]) -> Any:
        return await asyncio.to_thread(self._post_json_sync, payload)

    def _post_json_sync(self, payload: dict[str, Any]) -> Any:
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.endpoint,
            data=body,
            method="POST",
            headers={
                "content-type": "application/json",
                "accept": "application/json",
                "x-api-key": self.api_key or "",
                "anthropic-version": self.anthropic_version,
                **(self.runtime_profile.default_headers if self.runtime_profile else {}),
            },
        )
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.loads(response.read().decode("utf-8"))

    @property
    def endpoint(self) -> str:
        if self.base_url.endswith("/messages"):
            return self.base_url
        return f"{self.base_url}/messages"


def _response_dump(response: Any) -> Any:
    if isinstance(response, dict):
        return response
    if hasattr(response, "model_dump"):
        return response.model_dump()
    return response


def _value(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)
