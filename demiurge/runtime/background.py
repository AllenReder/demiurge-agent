from __future__ import annotations

import asyncio
from typing import Any


class BackgroundWorkRuntime:
    """Owns local background coroutines and optional durable task draining."""

    def __init__(self, task_worker: Any):
        self.task_worker = task_worker
        self._local_tasks: set[asyncio.Task[Any]] = set()

    def track(self, task: asyncio.Task[Any]) -> None:
        self._local_tasks.add(task)
        task.add_done_callback(self._local_tasks.discard)

    async def drain(self, *, include_runtime_tasks: bool = True) -> None:
        while self._local_tasks:
            await asyncio.gather(*list(self._local_tasks), return_exceptions=True)
        if include_runtime_tasks:
            await self.task_worker.drain()

    @property
    def active_count(self) -> int:
        local_count = sum(1 for task in self._local_tasks if not task.done())
        return local_count + int(getattr(self.task_worker, "active_count", 0))
