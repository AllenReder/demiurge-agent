from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Awaitable, Callable
from typing import Any


JsonEventSink = Callable[[str, dict[str, Any]], Awaitable[None]]
TUI_PROTOCOL_VERSION = 1
TUI_BUILD_STAMP = "demiurge-operator-v1"


class NdjsonRpcEndpoint:
    def __init__(self, *, reader=None, writer=None):
        self.reader = reader or sys.stdin
        self.writer = writer or sys.stdout
        self._write_lock = asyncio.Lock()

    async def write_event(self, event: str, payload: dict[str, Any] | None = None) -> None:
        await self.write({"event": event, "payload": payload or {}})

    async def write_result(self, message_id: object, result: Any = None) -> None:
        await self.write({"id": message_id, "result": result})

    async def write_error(self, message_id: object, message: str, *, code: str = "error") -> None:
        await self.write({"id": message_id, "error": {"code": code, "message": message}})

    async def write(self, payload: dict[str, Any]) -> None:
        line = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        async with self._write_lock:
            await asyncio.to_thread(self.writer.write, line)
            await asyncio.to_thread(self.writer.flush)

    async def iter_requests(self):
        while True:
            line = await asyncio.to_thread(self.reader.readline)
            if line == "":
                return
            stripped = line.strip()
            if not stripped:
                continue
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError as exc:
                await self.write_event(
                    "operator.error",
                    {"message": f"malformed json-rpc line: {exc}", "source": "gateway"},
                )
                continue
            yield payload
