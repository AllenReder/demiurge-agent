from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from demiurge.providers import ToolCall
from demiurge.sdk import ToolResult


@dataclass(slots=True)
class ToolExecutionRecord:
    call: ToolCall
    result: ToolResult


@dataclass(slots=True)
class BackgroundProcessRecord:
    process_id: str
    command: str
    cwd: str
    process: asyncio.subprocess.Process
    started_at: str
    output: list[str]
    reader_task: asyncio.Task[Any]
