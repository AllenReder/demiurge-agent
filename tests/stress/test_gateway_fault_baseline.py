from __future__ import annotations

import asyncio

import pytest

from baseline_support import BaselineContractFailure
from demiurge.channels import gateway as gateway_module
from demiurge.channels.gateway import GatewayChannel


pytestmark = pytest.mark.stress


class RepeatedPollingFailureBridge:
    def __init__(self, attempts_before_raise: int = 3) -> None:
        self.attempts_before_raise = attempts_before_raise
        self.attempts = 0
        self.third_attempt = asyncio.Event()

    async def run_forever(self) -> None:
        self.attempts += 1
        if self.attempts >= self.attempts_before_raise:
            self.third_attempt.set()
        raise ConnectionError(f"synthetic polling failure #{self.attempts}")

    async def send(self, target, text, *, metadata=None) -> None:
        raise AssertionError("fault baseline must not send channel output")


class HealthyBridge:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = False

    async def run_forever(self) -> None:
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise

    async def send(self, target, text, *, metadata=None) -> None:
        raise AssertionError("fault baseline must not send channel output")


class FakeScheduler:
    def __init__(self) -> None:
        self.stop_calls = 0

    async def stop(self) -> None:
        self.stop_calls += 1


class FakeApp:
    def __init__(self) -> None:
        self.close_calls = 0

    async def close(self) -> None:
        self.close_calls += 1


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    raises=BaselineContractFailure,
    reason="CH-04: polling failures are not retried independently from healthy peers and the scheduler",
)
async def test_ch_04_repeated_poll_failure_keeps_peers_and_scheduler_live(
    monkeypatch,
    baseline_recorder,
):
    fault = RepeatedPollingFailureBridge(attempts_before_raise=3)
    healthy = HealthyBridge()
    scheduler = FakeScheduler()
    app = FakeApp()
    real_sleep = asyncio.sleep

    async def skip_retry_delay(_delay):
        await real_sleep(0)

    monkeypatch.setattr(gateway_module.asyncio, "sleep", skip_retry_delay)
    monkeypatch.setattr(
        gateway_module,
        "build_enabled_gateway_channels",
        lambda _app: [
            GatewayChannel(name="fault", bridge=fault),
            GatewayChannel(name="healthy", bridge=healthy),
        ],
    )
    monkeypatch.setattr(
        gateway_module,
        "start_scheduler_for_app",
        lambda _app, *, delivery_route: scheduler,
    )

    gateway_task = None
    third_attempt_task = None
    guard_task = None
    try:
        with baseline_recorder.measure(
            "gateway_polling_failure_isolation",
            finding="CH-04",
            scale={"channels": 2, "fault_attempts": 3, "scheduler": 1},
        ) as sample:
            gateway_task = asyncio.create_task(gateway_module._run_gateway(app))
            await asyncio.wait_for(healthy.started.wait(), timeout=2)
            third_attempt_task = asyncio.create_task(fault.third_attempt.wait())
            guard_task = asyncio.create_task(real_sleep(2))
            await asyncio.wait(
                {gateway_task, third_attempt_task, guard_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            await real_sleep(0)
            gateway_exception = None
            if gateway_task.done():
                try:
                    gateway_task.result()
                except ConnectionError as exc:
                    if not str(exc).startswith("synthetic polling failure #"):
                        raise
                    gateway_exception = type(exc).__name__
            sample.observations.update(
                {
                    "fault_attempts": fault.attempts,
                    "third_attempt_reached": third_attempt_task.done(),
                    "gateway_pending": not gateway_task.done(),
                    "gateway_exception": gateway_exception,
                    "healthy_cancelled": healthy.cancelled,
                    "scheduler_stop_calls": scheduler.stop_calls,
                    "app_close_calls": app.close_calls,
                }
            )
            sample.require(
                third_attempt_task.done()
                and not gateway_task.done()
                and not healthy.cancelled
                and scheduler.stop_calls == 0
                and app.close_calls == 0,
                "repeated polling failures must be retried without stopping peers or scheduler",
            )
    finally:
        healthy.release.set()
        for task in (third_attempt_task, guard_task):
            if task is not None and not task.done():
                task.cancel()
        if gateway_task is not None and not gateway_task.done():
            gateway_task.cancel()
        await asyncio.gather(
            *(task for task in (gateway_task, third_attempt_task, guard_task) if task is not None),
            return_exceptions=True,
        )
