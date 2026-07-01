import asyncio
import shutil
from datetime import datetime, timezone

import pytest
import yaml

from demiurge.app import create_app, source_agents_root
from demiurge.channels.telegram import TelegramInteractionBridge
from demiurge.providers import LLMResponse
from demiurge.runtime.interactions import InteractionRuntime
from demiurge.scheduler import SchedulerService, SchedulerStore, next_fire_after, parse_instant, start_scheduler_for_app
from demiurge.storage import EventLog


UTC = timezone.utc


class RecordingProvider:
    def __init__(self, *, default: str = "main"):
        self.default = default
        self.requests = []

    async def complete(self, request):
        self.requests.append(request)
        return LLMResponse(content=self.default)


class ClarifyProvider(RecordingProvider):
    async def complete(self, request):
        from demiurge.providers import ToolCall

        self.requests.append(request)
        return LLMResponse(tool_calls=[ToolCall(id="ask_1", name="clarify", arguments={"question": "Which path?"})])


class FakeTelegramApi:
    def __init__(self):
        self.sent = []
        self.next_message_id = 1000

    def send_message(self, *, chat_id, text, reply_to_message_id=None, parse_mode=None, reply_markup=None):
        self.sent.append(
            {
                "chat_id": chat_id,
                "text": text,
                "reply_to_message_id": reply_to_message_id,
                "parse_mode": parse_mode,
                "reply_markup": reply_markup,
            }
        )
        self.next_message_id += 1
        return {"ok": True, "result": {"message_id": self.next_message_id}}


def _copy_agents(tmp_path):
    target = tmp_path / "agents"
    shutil.copytree(source_agents_root(), target)
    return target


def _write_schedule(
    agents,
    text,
    *,
    core_id="assistant",
    name="daily",
):
    schedule_dir = agents / core_id / "agent" / "schedules"
    schedule_dir.mkdir(parents=True, exist_ok=True)
    (schedule_dir / f"{name}.yaml").write_text(text, encoding="utf-8")


def _write_module(root, rel_path, code):
    path = root / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(code, encoding="utf-8")


def _write_slot(root, rel_path, text=None):
    path = root / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        text
        or "entrypoint: module:process\n"
        "description: test slot\n"
        "failure_policy: hard\n"
        "capabilities:\n"
        "  []\n",
        encoding="utf-8",
    )


def _write_pipeline(root, phase, *, serial, parallel=None, core_id="assistant"):
    parallel = parallel or []
    lines = ["serial:"]
    lines.extend(f"  - {slot_id}" for slot_id in serial)
    lines.append("parallel:")
    if parallel:
        lines.extend(f"  - {slot_id}" for slot_id in parallel)
    else:
        lines.append("  []")
    (root / core_id / "agent" / phase / "pipeline.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _schedule(app, schedule_id="daily"):
    core = app.core_loader.load(app.version_store.active_core_path(app.runner.core_id))
    return next(schedule for schedule in core.schedules if schedule.schedule_id == schedule_id)


def test_next_fire_after_uses_runtime_timezone(tmp_path):
    agents = _copy_agents(tmp_path)
    _write_schedule(
        agents,
        'schedule: "0 9 * * *"\n'
        'prompt: "Daily report"\n',
    )
    app = create_app(home=tmp_path / "home", provider_name="fake", agents_root=agents, timezone="Asia/Shanghai")
    schedule = _schedule(app)

    next_run = next_fire_after(schedule, datetime(2026, 6, 28, 0, 30, tzinfo=UTC), runtime_timezone=app.runtime_timezone)

    assert next_run == datetime(2026, 6, 28, 1, 0, tzinfo=UTC)


def test_scheduler_claim_coalesces_missed_runs_and_is_single_flight(tmp_path):
    agents = _copy_agents(tmp_path)
    _write_schedule(agents, 'schedule: "* * * * *"\nprompt: "Tick"\n')
    app = create_app(home=tmp_path / "home", provider_name="fake", agents_root=agents, timezone="UTC")
    schedule = _schedule(app)
    store = SchedulerStore(app.home, app.runner.core_id, runtime_timezone=app.runtime_timezone)
    due = datetime(2026, 6, 28, 10, 0, tzinfo=UTC)
    now = datetime(2026, 6, 28, 10, 5, 30, tzinfo=UTC)
    store.set_next_run(schedule, due)

    claim = store.claim_due(schedule, now=now)
    second_claim = store.claim_due(schedule, now=now)

    assert claim is not None
    assert claim.due_at == due
    assert second_claim is None
    next_run = parse_instant(store.read_state()["schedules"][schedule.schedule_id]["next_run_at"])
    assert next_run == datetime(2026, 6, 28, 10, 6, tzinfo=UTC)
    logs = store.read_run_logs()
    assert logs[0]["event"] == "claimed"
    assert logs[0]["run_id"] == claim.run_id
    assert logs[0]["due_at"] == "2026-06-28T10:00:00Z"
    assert logs[0]["due_at_local"] == "2026-06-28T10:00:00+00:00"
    assert logs[0]["runtime_timezone"] == "UTC"


@pytest.mark.asyncio
async def test_scheduler_run_uses_fresh_session_selected_modules_and_local_delivery(tmp_path):
    agents = _copy_agents(tmp_path)
    _write_module(
        agents,
        "assistant/agent/input/prefix/module.py",
        "def process(ctx):\n"
        "    ctx.input.add('user', 'PREFIX')\n",
    )
    _write_slot(agents, "assistant/agent/input/prefix/slot.yaml")
    _write_module(
        agents,
        "assistant/agent/output/extra/module.py",
        "def process(ctx):\n"
        "    ctx.output.send_text('extra')\n",
    )
    _write_slot(
        agents,
        "assistant/agent/output/extra/slot.yaml",
        "entrypoint: module:process\n"
        "description: extra output\n"
        "failure_policy: hard\n"
        "capabilities: []\n",
    )
    _write_pipeline(agents, "input", serial=["prefix", "base_input"])
    _write_pipeline(agents, "output", serial=["base_output", "extra"])
    _write_schedule(
        agents,
        'schedule: "* * * * *"\n'
        'prompt: "scheduled prompt"\n'
        "modules:\n"
        "  input: [base_input]\n"
        "  output: [base_output]\n",
    )
    app = create_app(home=tmp_path / "home", provider_name="fake", agents_root=agents, timezone="Asia/Shanghai")
    provider = RecordingProvider(default="model result")
    app.runner.provider = provider
    service = SchedulerService(app)
    schedule = _schedule(app)
    service.store.set_next_run(schedule, datetime(2026, 6, 28, 10, 0, tzinfo=UTC))

    results = await service.run_due_once(now=datetime(2026, 6, 28, 10, 1, tzinfo=UTC))

    assert len(results) == 1
    result = results[0]
    assert result.status == "completed"
    assert result.session_id is not None
    assert result.session_id != app.runner.session_id
    assert result.deliveries == 0
    user_messages = [message.content for message in provider.requests[0].messages if message.role == "user"]
    assert user_messages[-1] == "scheduled prompt"
    messages = app.runner.session_store.read_messages(result.session_id)
    assert [(message.role, message.content) for message in messages] == [
        ("user", "scheduled prompt"),
        ("assistant", "model result"),
    ]
    assert all(message.content != "extra" for message in messages)
    inbound = next(event for event in EventLog(app.home, result.session_id).read_all() if event["type"] == "message.inbound")
    assert inbound["trigger"] == "schedule"
    assert inbound["schedule_id"] == "daily"
    assert inbound["runtime_timezone"] == "Asia/Shanghai"
    assert inbound["runtime_timezone_source"] == "cli"
    assert inbound["due_at_local"] == "2026-06-28T18:00:00+08:00"
    assert inbound["scheduled_at_local"] == "2026-06-28T18:01:00+08:00"
    completed_log = service.store.read_run_logs()[-1]
    assert completed_log["event"] == "completed"
    assert completed_log["due_at"] == "2026-06-28T10:00:00Z"
    assert completed_log["due_at_local"] == "2026-06-28T18:00:00+08:00"
    assert completed_log["runtime_timezone"] == "Asia/Shanghai"


@pytest.mark.asyncio
async def test_scheduler_marks_needs_user_as_error(tmp_path):
    agents = _copy_agents(tmp_path)
    _write_schedule(agents, 'schedule: "* * * * *"\nprompt: "Ask"\n')
    app = create_app(home=tmp_path / "home", provider_name="fake", agents_root=agents)
    app.runner.provider = ClarifyProvider()
    service = SchedulerService(app)
    schedule = _schedule(app)
    service.store.set_next_run(schedule, datetime(2026, 6, 28, 10, 0, tzinfo=UTC))

    results = await service.run_due_once(now=datetime(2026, 6, 28, 10, 1, tzinfo=UTC))

    assert results[0].status == "error"
    assert "requested user input" in results[0].error
    assert service.store.read_run_logs()[-1]["event"] == "error"


@pytest.mark.asyncio
async def test_scheduler_telegram_delivery_uses_telegram_bridge(tmp_path):
    agents = _copy_agents(tmp_path)
    manifest_path = agents / "assistant" / "agent.yaml"
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    raw["channels"]["telegram"]["allowed_users"] = [123]
    manifest_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    _write_schedule(
        agents,
        'schedule: "* * * * *"\n'
        'prompt: "Send telegram"\n'
        "delivery:\n"
        "  mode: telegram\n"
        "  chat_id: 123\n",
    )
    app = create_app(home=tmp_path / "home", provider_name="fake", agents_root=agents)
    app.runner.provider = RecordingProvider(default="telegram output")
    api = FakeTelegramApi()
    bridge = TelegramInteractionBridge(
        runtime=InteractionRuntime(app.runner),
        api=api,
        message_format="plain",
        allowed_users=[123],
    )
    service = SchedulerService(app, delivery_bridge=bridge)
    schedule = _schedule(app)
    service.store.set_next_run(schedule, datetime(2026, 6, 28, 10, 0, tzinfo=UTC))

    results = await service.run_due_once(now=datetime(2026, 6, 28, 10, 1, tzinfo=UTC))

    assert results[0].status == "completed"
    assert results[0].deliveries == 1
    assert api.sent == [
        {
            "chat_id": "123",
            "text": "telegram output",
            "reply_to_message_id": None,
            "parse_mode": None,
            "reply_markup": None,
        }
    ]


@pytest.mark.asyncio
async def test_start_scheduler_for_app_runs_even_before_schedules_exist_and_loads_new_schedule(tmp_path):
    agents = _copy_agents(tmp_path)
    app = create_app(home=tmp_path / "home", provider_name="fake", agents_root=agents)
    app.runner.provider = RecordingProvider(default="late schedule output")
    service = start_scheduler_for_app(app, poll_interval_seconds=3600)
    try:
        assert service.running is True
        await asyncio.sleep(0)

        schedule_dir = app.version_store.active_core_path(app.runner.core_id) / "agent" / "schedules"
        schedule_dir.mkdir(parents=True, exist_ok=True)
        (schedule_dir / "daily.yaml").write_text('schedule: "* * * * *"\nprompt: "Late schedule"\n', encoding="utf-8")
        schedule = _schedule(app)
        service.store.set_next_run(schedule, datetime(2026, 6, 28, 10, 0, tzinfo=UTC))

        results = await service.run_due_once(now=datetime(2026, 6, 28, 10, 1, tzinfo=UTC))

        assert len(results) == 1
        assert results[0].status == "completed"
        user_messages = [message.content for message in app.runner.provider.requests[0].messages if message.role == "user"]
        assert user_messages[-1] == "Late schedule"
    finally:
        await service.stop()
