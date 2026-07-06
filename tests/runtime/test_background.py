from __future__ import annotations

import asyncio

import pytest

from demiurge.runtime.background import BackgroundWorkRuntime


class FakeTaskWorker:
    def __init__(self) -> None:
        self.active_count = 0
        self.drained = 0

    async def drain(self) -> None:
        self.drained += 1


@pytest.mark.asyncio
async def test_background_work_runtime_tracks_and_drains_local_tasks():
    worker = FakeTaskWorker()
    runtime = BackgroundWorkRuntime(worker)
    seen: list[str] = []

    async def work() -> None:
        await asyncio.sleep(0)
        seen.append("done")

    runtime.track(asyncio.create_task(work()))

    assert runtime.active_count == 1

    await runtime.drain(include_runtime_tasks=False)

    assert seen == ["done"]
    assert runtime.active_count == 0
    assert worker.drained == 0


@pytest.mark.asyncio
async def test_background_work_runtime_can_drain_durable_task_worker():
    worker = FakeTaskWorker()
    runtime = BackgroundWorkRuntime(worker)

    await runtime.drain()

    assert worker.drained == 1


def test_background_work_runtime_active_count_includes_durable_worker():
    worker = FakeTaskWorker()
    worker.active_count = 2
    runtime = BackgroundWorkRuntime(worker)

    assert runtime.active_count == 2
