from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from demiurge.providers.types import LLMMessage, LLMRequest, LLMResponse, ToolCall


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
