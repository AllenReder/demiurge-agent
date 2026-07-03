import shutil
from datetime import datetime, timezone

import pytest

from demiurge.app import create_app, source_agents_root
from demiurge.providers import LLMResponse, ToolCall
from demiurge.scheduler import SchedulerService


UTC = timezone.utc


class RecordingProvider:
    async def complete(self, request):
        return LLMResponse(content="scheduled output")


class ClarifyProvider:
    async def complete(self, request):
        return LLMResponse(tool_calls=[ToolCall(id="ask_1", name="clarify", arguments={"question": "Which path?"})])


def _copy_agents(tmp_path):
    target = tmp_path / "agents"
    shutil.copytree(source_agents_root(), target)
    return target


def _write_schedule(agents, text, *, core_id="assistant", name="daily"):
    schedule_dir = agents / core_id / "agent" / "schedules"
    schedule_dir.mkdir(parents=True, exist_ok=True)
    (schedule_dir / f"{name}.yaml").write_text(text, encoding="utf-8")


def _schedule(app, schedule_id="daily"):
    core = app.core_loader.load(app.version_store.active_core_path(app.runner.core_id))
    return next(schedule for schedule in core.schedules if schedule.schedule_id == schedule_id)


@pytest.mark.asyncio
async def test_completed_schedule_run_writes_one_user_visible_run_log(tmp_path):
    agents = _copy_agents(tmp_path)
    _write_schedule(agents, 'schedule: "* * * * *"\nprompt: "Daily report"\n')
    app = create_app(home=tmp_path / "home", provider_name="fake", agents_root=agents, timezone="UTC")
    app.runner.provider = RecordingProvider()
    service = SchedulerService(app)
    schedule = _schedule(app)
    due_at = datetime(2026, 6, 28, 10, 0, tzinfo=UTC)
    service.store.set_next_run(schedule, due_at)

    results = await service.run_due_once(now=datetime(2026, 6, 28, 10, 1, tzinfo=UTC))

    assert len(results) == 1
    result = results[0]
    assert app.control_plane.read(result.run_id)["status"] == "succeeded"
    completed_logs = [
        log
        for log in service.store.read_run_logs()
        if log["event"] == "completed" and log["run_id"] == result.run_id and log["due_at"] == result.due_at
    ]
    assert len(completed_logs) == 1


@pytest.mark.asyncio
async def test_error_schedule_run_writes_one_user_visible_run_log(tmp_path):
    agents = _copy_agents(tmp_path)
    _write_schedule(agents, 'schedule: "* * * * *"\nprompt: "Ask user"\n')
    app = create_app(home=tmp_path / "home", provider_name="fake", agents_root=agents, timezone="UTC")
    app.runner.provider = ClarifyProvider()
    service = SchedulerService(app)
    schedule = _schedule(app)
    due_at = datetime(2026, 6, 28, 10, 0, tzinfo=UTC)
    service.store.set_next_run(schedule, due_at)

    results = await service.run_due_once(now=datetime(2026, 6, 28, 10, 1, tzinfo=UTC))

    assert len(results) == 1
    result = results[0]
    assert app.control_plane.read(result.run_id)["status"] == "failed"
    error_logs = [
        log
        for log in service.store.read_run_logs()
        if log["event"] == "error" and log["run_id"] == result.run_id and log["due_at"] == result.due_at
    ]
    assert len(error_logs) == 1
