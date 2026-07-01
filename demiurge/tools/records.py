from __future__ import annotations

from dataclasses import dataclass

from demiurge.providers import ToolCall
from demiurge.sdk import ToolResult


@dataclass(slots=True)
class ToolExecutionRecord:
    call: ToolCall
    result: ToolResult
