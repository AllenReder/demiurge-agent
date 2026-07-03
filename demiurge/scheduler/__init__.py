from __future__ import annotations

import asyncio
import contextlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from croniter import croniter

from demiurge.core import LoadedCore, ScheduleDefinition
from demiurge.runtime.control import ActionSource, ActionSpec, RuntimeControlPlane
from demiurge.runtime.interactions import InteractionInbound, InteractionOutbound, InteractionRuntime
from demiurge.runtime.runner import SessionTurnStepRunner
from demiurge.runtime.store import RuntimeEvent, RuntimeQuery
from demiurge.runtime_timezone import RuntimeTimezone, resolve_runtime_timezone
from demiurge.util import utc_id


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


def next_fire_after(
    schedule: ScheduleDefinition,
    after: datetime,
    *,
    runtime_timezone: RuntimeTimezone | None = None,
) -> datetime:
    resolved_timezone = runtime_timezone or resolve_runtime_timezone()
    zone = resolved_timezone.zone
    base = after.astimezone(zone)
    next_value = croniter(schedule.schedule, base).get_next(datetime)
    if next_value.tzinfo is None:
        next_value = next_value.replace(tzinfo=zone)
    return next_value.astimezone(UTC)


def schedule_signature(schedule: ScheduleDefinition) -> str:
    data = {
        "schedule": schedule.schedule,
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


class SchedulerRuntime:
    def __init__(
        self,
        control_plane: RuntimeControlPlane,
        core_id: str,
        *,
        runtime_timezone: RuntimeTimezone | None = None,
    ):
        self.control_plane = control_plane
        self.core_id = core_id
        self.runtime_timezone = runtime_timezone or resolve_runtime_timezone()

    def set_next_run(self, schedule: ScheduleDefinition, next_run_at: datetime) -> None:
        self._record_instance(
            schedule,
            due_at=next_run_at.astimezone(UTC),
            task_id=None,
            claim_status="scheduled",
            event_type="scheduler.scheduled",
        )

    def claim_due(self, schedule: ScheduleDefinition, *, now: datetime | None = None) -> ScheduleRunClaim | None:
        if not schedule.enabled:
            return None
        now = (now or datetime.now(UTC)).astimezone(UTC)
        signature = self._signature(schedule)
        rows = [row for row in self._rows(schedule.schedule_id) if row.get("idempotency_key") == signature]
        scheduled = sorted(
            [row for row in rows if row.get("claim_status") == "scheduled"],
            key=lambda row: row["due_at"],
        )
        if not scheduled:
            self.set_next_run(schedule, next_fire_after(schedule, now, runtime_timezone=self.runtime_timezone))
            return None
        due_rows = [row for row in scheduled if parse_instant(str(row["due_at"])) <= now]
        if not due_rows:
            return None
        row = due_rows[0]
        due_at = parse_instant(str(row["due_at"]))
        run_id = utc_id("schedule_run_")
        scheduled_at = now
        aggregate_id = self._instance_aggregate_id(schedule, due_at)
        last_seq = self._last_instance_seq(aggregate_id)
        if last_seq is None:
            return None
        try:
            self._record_instance(
                schedule,
                due_at=due_at,
                task_id=run_id,
                claim_status="claimed",
                event_type="scheduler.claimed",
                idempotency_key=f"scheduler:{self.core_id}:{schedule.schedule_id}:{format_instant(due_at)}:claim:{run_id}",
                expected={
                    "aggregate_type": "scheduler_instance",
                    "aggregate_id": aggregate_id,
                    "last_seq": last_seq,
                },
            )
        except RuntimeError:
            return None
        next_run_at = next_fire_after(schedule, now, runtime_timezone=self.runtime_timezone)
        if not any(parse_instant(str(item["due_at"])) == next_run_at for item in scheduled):
            self.set_next_run(schedule, next_run_at)
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
        self.control_plane.store.append(
            [
                RuntimeEvent(
                    type="scheduler.completed" if status == "completed" else "scheduler.error",
                    aggregate_type="scheduler_instance",
                    aggregate_id=claim.run_id,
                    payload={
                        "core_id": self.core_id,
                        "schedule_id": claim.schedule_id,
                        "due_at": format_instant(claim.due_at),
                        "task_id": claim.run_id,
                        "claim_status": status,
                        "session_id": session_id,
                        "turn_id": turn_id,
                        "deliveries": deliveries,
                        "error": error,
                    },
                )
            ]
        )

    def read_run_logs(self) -> list[dict[str, Any]]:
        events = self.control_plane.store.query(
            RuntimeQuery(table="runtime_events", where={"aggregate_type": "scheduler_instance"}, order_by="seq", limit=10_000)
        ).rows
        return [
            {
                "event": event["type"].split(".", 1)[1],
                "status": (event.get("payload") or {}).get("claim_status") or event["type"].split(".", 1)[1],
                "core_id": (event.get("payload") or {}).get("core_id"),
                "schedule_id": (event.get("payload") or {}).get("schedule_id"),
                "run_id": (event.get("payload") or {}).get("task_id"),
                "due_at": (event.get("payload") or {}).get("due_at"),
                "due_at_local": self.runtime_timezone.format_local(parse_instant(str((event.get("payload") or {}).get("due_at"))))
                if (event.get("payload") or {}).get("due_at")
                else None,
                "runtime_timezone": self.runtime_timezone.name,
            }
            for event in events
            if (event.get("payload") or {}).get("core_id") == self.core_id
        ]

    def _rows(self, schedule_id: str) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.control_plane.store.query(
                RuntimeQuery(table="scheduler_instances", where={"core_id": self.core_id, "schedule_id": schedule_id}, limit=10_000)
            ).rows
        ]

    def _signature(self, schedule: ScheduleDefinition) -> str:
        return f"{self.runtime_timezone.name}:{schedule_signature(schedule)}"

    def _record_instance(
        self,
        schedule: ScheduleDefinition,
        *,
        due_at: datetime,
        task_id: str | None,
        claim_status: str,
        event_type: str,
        idempotency_key: str | None = None,
        expected: dict[str, Any] | None = None,
    ) -> None:
        self.control_plane.store.append(
            [
                RuntimeEvent(
                    type=event_type,
                    aggregate_type="scheduler_instance",
                    aggregate_id=self._instance_aggregate_id(schedule, due_at),
                    payload={
                        "core_id": self.core_id,
                        "schedule_id": schedule.schedule_id,
                        "due_at": format_instant(due_at),
                        "task_id": task_id,
                        "claim_status": claim_status,
                        "idempotency_key": self._signature(schedule),
                    },
                )
            ],
            idempotency_key=idempotency_key
            or f"scheduler:{self.core_id}:{schedule.schedule_id}:{format_instant(due_at)}:{claim_status}",
            expected=expected,
        )

    def _instance_aggregate_id(self, schedule: ScheduleDefinition, due_at: datetime) -> str:
        return f"{self.core_id}:{schedule.schedule_id}:{format_instant(due_at)}"

    def _last_instance_seq(self, aggregate_id: str) -> int | None:
        rows = self.control_plane.store.query(
            RuntimeQuery(
                table="runtime_events",
                where={"aggregate_type": "scheduler_instance", "aggregate_id": aggregate_id},
                order_by="seq",
                limit=10_000,
            )
        ).rows
        if not rows:
            return None
        return int(rows[-1]["seq"])


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
        self.store = SchedulerRuntime(app.control_plane, app.runner.core_id, runtime_timezone=app.runtime_timezone)
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
        core = await self._load_core()
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

    async def _load_core(self) -> LoadedCore:
        return await self.app.load_active_core()

    async def _run_claim(
        self,
        core: LoadedCore,
        schedule: ScheduleDefinition,
        claim: ScheduleRunClaim,
    ) -> ScheduleRunResult:
        session_id: str | None = None
        turn_id: str | None = None
        deliveries = 0
        task_id = claim.run_id
        self._record_claim_task(core, schedule, claim, task_id=task_id)
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
            self._record_claim_completed(
                core,
                schedule,
                claim,
                task_id=task_id,
                status="completed",
                result_ref=f"session:{session_id}:{turn_id}",
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
            self._record_claim_completed(
                core,
                schedule,
                claim,
                task_id=task_id,
                status="error",
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
            runtime_timezone=self.app.runtime_timezone,
            task_worker=self.app.task_worker,
            session_runtime=self.app.session_runtime,
            prepare_live_core=self.app.prepare_live_core,
        )

    def _record_claim_task(
        self,
        core: LoadedCore,
        schedule: ScheduleDefinition,
        claim: ScheduleRunClaim,
        *,
        task_id: str,
    ) -> None:
        control_plane = getattr(self.app, "control_plane", None)
        if control_plane is None:
            return
        idempotency_key = f"schedule:{core.core_id}:{schedule.schedule_id}:{format_instant(claim.due_at)}"
        control_plane.submit(
            ActionSpec(
                kind="schedule.fire",
                payload={
                    "task_id": task_id,
                    "core_id": core.core_id,
                    "owner_session_id": None,
                    "notify_policy": "schedule_delivery",
                    "schedule_id": schedule.schedule_id,
                    "due_at": format_instant(claim.due_at),
                    "scheduled_at": format_instant(claim.scheduled_at),
                },
                idempotency_key=idempotency_key,
            ),
            source=ActionSource(actor="host.scheduler", core_id=core.core_id),
        )
        control_plane.mark_started(
            task_id,
            source=ActionSource(actor="host.scheduler", core_id=core.core_id, task_id=task_id),
        )

    def _record_claim_completed(
        self,
        core: LoadedCore,
        schedule: ScheduleDefinition,
        claim: ScheduleRunClaim,
        *,
        task_id: str,
        status: str,
        result_ref: str | None = None,
        error: str | None = None,
    ) -> None:
        control_plane = getattr(self.app, "control_plane", None)
        if control_plane is None:
            return
        if status == "completed":
            control_plane.succeed(
                task_id,
                result_ref=result_ref,
                source=ActionSource(actor="host.scheduler", core_id=core.core_id, task_id=task_id),
            )
        else:
            control_plane.fail(
                task_id,
                error=error or "scheduled task failed",
                source=ActionSource(actor="host.scheduler", core_id=core.core_id, task_id=task_id),
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
            "due_at_local": self.store.runtime_timezone.format_local(claim.due_at),
            "scheduled_at": format_instant(claim.scheduled_at),
            "scheduled_at_local": self.store.runtime_timezone.format_local(claim.scheduled_at),
            "delivery_mode": schedule.delivery.mode,
            "delivery_channel": schedule.delivery.channel_name,
            "delivery_target": schedule.delivery.delivery_target,
            **self.store.runtime_timezone.metadata(now=claim.scheduled_at),
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
) -> SchedulerService:
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
    "SchedulerRuntime",
    "format_instant",
    "next_fire_after",
    "parse_instant",
    "schedule_signature",
    "start_scheduler_for_app",
]
