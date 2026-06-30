from __future__ import annotations

import asyncio
import contextlib
import fcntl
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from zoneinfo import ZoneInfo

from croniter import croniter

from demiurge.core import LoadedCore, ScheduleDefinition
from demiurge.runtime.interactions import InteractionInbound, InteractionOutbound, InteractionRuntime
from demiurge.runtime.runner import SessionTurnStepRunner
from demiurge.util import append_jsonl, ensure_dir, read_json, utc_id, write_json


UTC = timezone.utc


@dataclass(slots=True)
class ScheduleRunClaim:
    run_id: str
    schedule_id: str
    due_at: datetime
    scheduled_at: datetime


@dataclass(slots=True)
class ScheduleRunResult:
    run_id: str
    schedule_id: str
    status: str
    due_at: str
    scheduled_at: str
    session_id: str | None = None
    turn_id: str | None = None
    deliveries: int = 0
    error: str | None = None


def parse_instant(value: str) -> datetime:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def format_instant(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def next_fire_after(schedule: ScheduleDefinition, after: datetime) -> datetime:
    zone = ZoneInfo(schedule.timezone)
    base = after.astimezone(zone)
    next_value = croniter(schedule.schedule, base).get_next(datetime)
    if next_value.tzinfo is None:
        next_value = next_value.replace(tzinfo=zone)
    return next_value.astimezone(UTC)


def schedule_signature(schedule: ScheduleDefinition) -> str:
    data = {
        "schedule": schedule.schedule,
        "timezone": schedule.timezone,
        "prompt": schedule.prompt,
        "modules": {
            "input": list(schedule.modules.input),
            "output": list(schedule.modules.output),
        },
        "delivery": {
            "mode": schedule.delivery.mode,
            "channel": schedule.delivery.channel,
            "target": schedule.delivery.target,
            "chat_id": schedule.delivery.chat_id,
        },
    }
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class SchedulerStore:
    def __init__(self, home: Path, core_id: str):
        self.home = home
        self.core_id = core_id
        self.root = home / "scheduler" / core_id
        self.state_path = self.root / "state.json"
        self.runs_path = self.root / "runs.jsonl"
        self.lock_path = self.root / "lock"

    @contextlib.contextmanager
    def locked(self) -> Iterator[None]:
        ensure_dir(self.root)
        with self.lock_path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def read_state(self) -> dict[str, Any]:
        state = read_json(self.state_path, None)
        if not isinstance(state, dict):
            return {"schema_version": 1, "schedules": {}}
        state.setdefault("schema_version", 1)
        schedules = state.setdefault("schedules", {})
        if not isinstance(schedules, dict):
            state["schedules"] = {}
        return state

    def write_state(self, state: dict[str, Any]) -> None:
        write_json(self.state_path, state)

    def set_next_run(self, schedule: ScheduleDefinition, next_run_at: datetime) -> None:
        with self.locked():
            state = self.read_state()
            state.setdefault("schedules", {})[schedule.schedule_id] = {
                "schedule_id": schedule.schedule_id,
                "signature": schedule_signature(schedule),
                "enabled": schedule.enabled,
                "next_run_at": format_instant(next_run_at),
            }
            self.write_state(state)

    def claim_due(self, schedule: ScheduleDefinition, *, now: datetime | None = None) -> ScheduleRunClaim | None:
        if not schedule.enabled:
            return None
        now = (now or datetime.now(UTC)).astimezone(UTC)
        with self.locked():
            state = self.read_state()
            schedules = state.setdefault("schedules", {})
            entry = schedules.get(schedule.schedule_id)
            signature = schedule_signature(schedule)
            if not isinstance(entry, dict) or entry.get("signature") != signature:
                schedules[schedule.schedule_id] = {
                    "schedule_id": schedule.schedule_id,
                    "signature": signature,
                    "enabled": schedule.enabled,
                    "next_run_at": format_instant(next_fire_after(schedule, now)),
                }
                self.write_state(state)
                return None

            next_run_raw = entry.get("next_run_at")
            if not isinstance(next_run_raw, str) or not next_run_raw:
                entry["next_run_at"] = format_instant(next_fire_after(schedule, now))
                self.write_state(state)
                return None
            due_at = parse_instant(next_run_raw)
            if due_at > now:
                return None

            run_id = utc_id("schedule_run_")
            scheduled_at = now
            next_run_at = next_fire_after(schedule, now)
            entry.update(
                {
                    "enabled": schedule.enabled,
                    "last_claimed_run_id": run_id,
                    "last_due_at": format_instant(due_at),
                    "last_scheduled_at": format_instant(scheduled_at),
                    "next_run_at": format_instant(next_run_at),
                }
            )
            self.write_state(state)
            self._append_run_log(
                {
                    "event": "claimed",
                    "status": "claimed",
                    "core_id": self.core_id,
                    "schedule_id": schedule.schedule_id,
                    "run_id": run_id,
                    "due_at": format_instant(due_at),
                    "scheduled_at": format_instant(scheduled_at),
                    "next_run_at": format_instant(next_run_at),
                }
            )
            return ScheduleRunClaim(
                run_id=run_id,
                schedule_id=schedule.schedule_id,
                due_at=due_at,
                scheduled_at=scheduled_at,
            )

    def record_completed(
        self,
        claim: ScheduleRunClaim,
        *,
        status: str,
        session_id: str | None = None,
        turn_id: str | None = None,
        deliveries: int = 0,
        error: str | None = None,
    ) -> None:
        completed_at = datetime.now(UTC)
        with self.locked():
            state = self.read_state()
            entry = state.setdefault("schedules", {}).setdefault(claim.schedule_id, {})
            entry.update(
                {
                    "last_run_id": claim.run_id,
                    "last_status": status,
                    "last_completed_at": format_instant(completed_at),
                    "last_session_id": session_id,
                    "last_turn_id": turn_id,
                }
            )
            self.write_state(state)
            self._append_run_log(
                {
                    "event": status,
                    "status": status,
                    "core_id": self.core_id,
                    "schedule_id": claim.schedule_id,
                    "run_id": claim.run_id,
                    "due_at": format_instant(claim.due_at),
                    "scheduled_at": format_instant(claim.scheduled_at),
                    "completed_at": format_instant(completed_at),
                    "session_id": session_id,
                    "turn_id": turn_id,
                    "deliveries": deliveries,
                    **({"error": error} if error else {}),
                }
            )

    def read_run_logs(self) -> list[dict[str, Any]]:
        if not self.runs_path.exists():
            return []
        return [json.loads(line) for line in self.runs_path.read_text(encoding="utf-8").splitlines()]

    def _append_run_log(self, entry: dict[str, Any]) -> None:
        append_jsonl(self.runs_path, entry)


class SchedulerService:
    def __init__(
        self,
        app: Any,
        *,
        delivery_bridge: Any | None = None,
        poll_interval_seconds: float = 30.0,
    ):
        self.app = app
        self.delivery_bridge = delivery_bridge
        self.poll_interval_seconds = poll_interval_seconds
        self.store = SchedulerStore(app.home, app.runner.core_id)
        self._task: asyncio.Task[None] | None = None
        self._bridges: dict[str, Any] = {}

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        if not self.running:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        task = self._task
        self._task = None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def run_due_once(self, *, now: datetime | None = None) -> list[ScheduleRunResult]:
        core = self._load_core()
        results: list[ScheduleRunResult] = []
        for schedule in core.schedules:
            if not schedule.enabled:
                continue
            claim = self.store.claim_due(schedule, now=now)
            if claim is None:
                continue
            results.append(await self._run_claim(core, schedule, claim))
        return results

    async def _loop(self) -> None:
        while True:
            try:
                await self.run_due_once()
            except Exception:
                pass
            await asyncio.sleep(self.poll_interval_seconds)

    def _load_core(self) -> LoadedCore:
        return self.app.core_loader.load(self.app.version_store.active_core_path(self.app.runner.core_id))

    async def _run_claim(
        self,
        core: LoadedCore,
        schedule: ScheduleDefinition,
        claim: ScheduleRunClaim,
    ) -> ScheduleRunResult:
        session_id: str | None = None
        turn_id: str | None = None
        deliveries = 0
        try:
            if schedule.delivery.mode != "local":
                self._require_channel_target_allowed(core, schedule)
            runner = self._new_run_runner()
            inbound = self._schedule_inbound(schedule, claim)
            result = await runner.run_turn(
                schedule.prompt,
                interaction=inbound,
                input_slot_ids=schedule.modules.input,
                output_slot_ids=schedule.modules.output,
            )
            session_id = result.session_id
            turn_id = result.turn_id
            if result.needs_user:
                raise RuntimeError("schedule run requested user input")
            deliveries = await self._deliver_if_needed(core, schedule, claim, result)
            self.store.record_completed(
                claim,
                status="completed",
                session_id=session_id,
                turn_id=turn_id,
                deliveries=deliveries,
            )
            return ScheduleRunResult(
                run_id=claim.run_id,
                schedule_id=claim.schedule_id,
                status="completed",
                due_at=format_instant(claim.due_at),
                scheduled_at=format_instant(claim.scheduled_at),
                session_id=session_id,
                turn_id=turn_id,
                deliveries=deliveries,
            )
        except Exception as exc:
            self.store.record_completed(
                claim,
                status="error",
                session_id=session_id,
                turn_id=turn_id,
                deliveries=deliveries,
                error=str(exc),
            )
            return ScheduleRunResult(
                run_id=claim.run_id,
                schedule_id=claim.schedule_id,
                status="error",
                due_at=format_instant(claim.due_at),
                scheduled_at=format_instant(claim.scheduled_at),
                session_id=session_id,
                turn_id=turn_id,
                deliveries=deliveries,
                error=str(exc),
            )

    def _new_run_runner(self) -> SessionTurnStepRunner:
        return SessionTurnStepRunner(
            home=self.app.home,
            version_store=self.app.version_store,
            core_loader=self.app.core_loader,
            provider=self.app.runner.provider,
            tool_runtime=self.app.tool_runtime,
            core_id=self.app.runner.core_id,
            session_id=utc_id("session_schedule_"),
            model_override=self.app.runner.model_override,
            model_resolver=self.app.runner.model_resolver,
            provider_name=self.app.runner.provider_name,
            workspace=self.app.runner.workspace,
            show_system_prompt=self.app.runner.show_system_prompt,
        )

    def _schedule_inbound(self, schedule: ScheduleDefinition, claim: ScheduleRunClaim) -> InteractionInbound:
        metadata = self._schedule_metadata(schedule, claim)
        if schedule.delivery.mode == "local":
            return InteractionInbound(
                channel="schedule",
                text=schedule.prompt,
                source=schedule.schedule_id,
                conversation_key=None,
                metadata=metadata,
            )
        channel = schedule.delivery.channel_name
        target = schedule.delivery.delivery_target
        assert target is not None
        metadata.update({f"{channel}_target": target})
        if channel == "telegram" and schedule.delivery.chat_id is not None:
            metadata.update({"telegram_chat_id": schedule.delivery.chat_id})
        return InteractionInbound(
            channel=channel,
            text=schedule.prompt,
            source=target,
            conversation_key=None,
            metadata=metadata,
        )

    def _schedule_metadata(self, schedule: ScheduleDefinition, claim: ScheduleRunClaim) -> dict[str, Any]:
        return {
            "trigger": "schedule",
            "schedule_id": schedule.schedule_id,
            "run_id": claim.run_id,
            "due_at": format_instant(claim.due_at),
            "scheduled_at": format_instant(claim.scheduled_at),
            "delivery_mode": schedule.delivery.mode,
            "delivery_channel": schedule.delivery.channel_name,
            "delivery_target": schedule.delivery.delivery_target,
        }

    async def _deliver_if_needed(
        self,
        core: LoadedCore,
        schedule: ScheduleDefinition,
        claim: ScheduleRunClaim,
        result: Any,
    ) -> int:
        if schedule.delivery.mode == "local":
            return 0
        channel = schedule.delivery.channel_name
        target = schedule.delivery.delivery_target
        assert target is not None
        bridge = self.delivery_bridge or self._get_channel_bridge(core, channel)
        metadata = {
            "source": str(target),
            f"{channel}_target": target,
            **self._schedule_metadata(schedule, claim),
        }
        if channel == "telegram" and schedule.delivery.chat_id is not None:
            metadata["telegram_chat_id"] = schedule.delivery.chat_id
        await bridge.deliver(
            InteractionOutbound(
                channel=channel,
                items=list(result.items),
                session_id=result.session_id,
                turn_id=result.turn_id,
                metadata=metadata,
            )
        )
        return len(result.deliveries)

    def _get_channel_bridge(self, core: LoadedCore, channel: str) -> Any:
        bridge = self._bridges.get(channel)
        if bridge is not None:
            return bridge
        from demiurge.channels.registry import build_channel_bridge

        config = core.manifest.channels.get(channel)
        if config is None:
            raise RuntimeError(f"{channel} schedule delivery requires channels.{channel}")
        self._bridges[channel] = build_channel_bridge(self.app, channel, config)
        return self._bridges[channel]

    def _require_channel_target_allowed(self, core: LoadedCore, schedule: ScheduleDefinition) -> None:
        channel = schedule.delivery.channel_name
        config = core.manifest.channels.get(channel)
        if config is None:
            raise RuntimeError(f"{channel} schedule delivery requires channels.{channel}")
        from demiurge.channels.registry import validate_schedule_target

        validate_schedule_target(channel, config, schedule.delivery)


def start_scheduler_for_app(
    app: Any,
    *,
    delivery_bridge: Any | None = None,
    poll_interval_seconds: float = 30.0,
) -> SchedulerService | None:
    core = app.core_loader.load(app.version_store.active_core_path(app.runner.core_id))
    if not any(schedule.enabled for schedule in core.schedules):
        return None
    service = SchedulerService(
        app,
        delivery_bridge=delivery_bridge,
        poll_interval_seconds=poll_interval_seconds,
    )
    service.start()
    return service


__all__ = [
    "ScheduleRunClaim",
    "ScheduleRunResult",
    "SchedulerService",
    "SchedulerStore",
    "format_instant",
    "next_fire_after",
    "parse_instant",
    "schedule_signature",
    "start_scheduler_for_app",
]
