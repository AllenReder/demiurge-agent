from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass(slots=True)
class ToolCall:
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    id: str = "tool_call"


@dataclass(slots=True)
class LLMMessage:
    role: str
    content: str
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    persist: bool = True


@dataclass(slots=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(slots=True)
class LLMRequest:
    model: str
    messages: list[LLMMessage]
    tools: list[ToolDefinition] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class LLMResponse:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw: Any | None = None


class Provider(Protocol):
    async def complete(self, request: LLMRequest) -> LLMResponse:
        ...


class FakeProvider:
    def __init__(self, script_path: Path | None = None):
        self.script_path = script_path
        self.responses = self._load_responses(script_path)
        self.index = 0

    async def complete(self, request: LLMRequest) -> LLMResponse:
        if self.index < len(self.responses):
            item = self.responses[self.index]
            self.index += 1
            return self._response_from_dict(item)
        last_user_index, user_text = self._last_user_message(request.messages)
        current_turn_tail = request.messages[last_user_index + 1 :] if last_user_index >= 0 else request.messages
        has_tool_result = any(msg.role == "tool" for msg in current_turn_tail)
        if "tools_list" in user_text and not has_tool_result and any(tool.name == "tools_list" for tool in request.tools):
            return LLMResponse(
                tool_calls=[
                    ToolCall(
                        id="fake_tool_call_1",
                        name="tools_list",
                        arguments={},
                    )
                ]
            )
        if has_tool_result:
            tool_text = next((msg.content for msg in reversed(current_turn_tail) if msg.role == "tool"), "")
            return LLMResponse(content=f"[fake] tool result received: {tool_text}")
        return LLMResponse(content=f"[fake] {user_text}")

    def _last_user_message(self, messages: list[LLMMessage]) -> tuple[int, str]:
        for index in range(len(messages) - 1, -1, -1):
            message = messages[index]
            if message.role == "user":
                return index, message.content
        return -1, ""

    def _load_responses(self, script_path: Path | None) -> list[dict[str, Any]]:
        if not script_path or not script_path.exists():
            return []
        data = json.loads(script_path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
        return list(data.get("responses", []))

    def _response_from_dict(self, item: dict[str, Any]) -> LLMResponse:
        calls = [
            ToolCall(
                id=call.get("id", f"fake_tool_call_{idx}"),
                name=call["name"],
                arguments=call.get("arguments", {}),
            )
            for idx, call in enumerate(item.get("tool_calls", []), start=1)
        ]
        return LLMResponse(content=item.get("content", ""), tool_calls=calls, raw=item)


class OpenAICompatibleProvider:
    def __init__(self, *, api_key: str | None = None, base_url: str | None = None):
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self.base_url = base_url or os.environ.get("OPENAI_BASE_URL")
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY is required for the OpenAI-compatible provider")

    async def complete(self, request: LLMRequest) -> LLMResponse:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url)
        response = await client.chat.completions.create(
            model=request.model,
            messages=[self._to_openai_message(message) for message in request.messages],
            tools=[self._to_openai_tool(tool) for tool in request.tools] or None,
        )
        message = response.choices[0].message
        calls = []
        for call in message.tool_calls or []:
            arguments: dict[str, Any]
            try:
                arguments = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                arguments = {"_raw": call.function.arguments or ""}
            calls.append(ToolCall(id=call.id, name=call.function.name, arguments=arguments))
        return LLMResponse(content=message.content or "", tool_calls=calls, raw=response.model_dump())

    def _to_openai_message(self, message: LLMMessage) -> dict[str, Any]:
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

    def _to_openai_tool(self, tool: ToolDefinition) -> dict[str, Any]:
        schema = tool.input_schema or {"type": "object", "properties": {}}
        return {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": schema,
            },
        }
