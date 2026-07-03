import json
from pathlib import Path

import yaml
import pytest

from demiurge.app import create_app
from demiurge.evolution import EvolverRunResult
from demiurge.security.approval import ApprovalDecision, StaticApprovalProvider
from demiurge.security.capabilities import CapabilityFacade
from demiurge.core import BUILTIN_TOOLSETS
from demiurge.providers import ToolCall
from demiurge.sdk import AgentInput, TurnContext
from demiurge.tools import runtime as tool_runtime
from demiurge.tools.registry import BUILTIN_TOOL_DEFINITIONS


class RecordingApprovalProvider:
    name = "recording"

    def __init__(self, decisions: list[str]):
        self.decisions = decisions
        self.requests = []

    def decide(self, request):
        self.requests.append(request)
        if self.decisions:
            return ApprovalDecision(self.decisions.pop(0), "recorded test decision")
        return ApprovalDecision("deny", "no recorded decision left")


def _load_core_with(
    app,
    *,
    toolsets: list[str] | None = None,
    capabilities: dict[str, dict] | None = None,
):
    manifest_path = app.version_store.active_core_path("assistant") / "agent.yaml"
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if toolsets is not None:
        raw.setdefault("tools", {})["toolsets"] = toolsets
    defaults = raw.setdefault("capabilities", {}).setdefault("defaults", {})
    for capability, value in (capabilities or {}).items():
        defaults[capability] = value
    manifest_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return app.core_loader.load(app.version_store.active_core_path("assistant"))


def _install_test_skills(app):
    root = app.version_store.active_core_path("assistant") / "agent" / "skills"
    debugging = root / "debugging"
    references = debugging / "references"
    references.mkdir(parents=True, exist_ok=True)
    (debugging / "SKILL.md").write_text(
        "---\n"
        "name: debugging\n"
        "description: Debug failing commands.\n"
        "category: development\n"
        "---\n\n"
        "# Debugging\n\n"
        "Start from the exact failing command.\n",
        encoding="utf-8",
    )
    (references / "checklist.md").write_text("# Debugging Checklist\n\n- Reproduce the failure.\n", encoding="utf-8")
    (root / "project-notes.md").write_text(
        "---\n"
        "name: project-notes\n"
        "description: Summarize project context.\n"
        "category: development\n"
        "---\n\n"
        "# Project Notes\n\n"
        "Use this skill when project context matters.\n",
        encoding="utf-8",
    )


def _turn(core):
    return TurnContext(
        session_id="session_test",
        turn_id="turn_test",
        core_id=core.core_id,
        core_revision=core.revision,
        user_input=AgentInput(content="test"),
        state={},
    )


async def _execute(app, core, name, arguments):
    return await app.tool_runtime.execute(
        ToolCall(name=name, arguments=arguments, id=f"call_{name}"),
        core=core,
        turn=_turn(core),
        capability=CapabilityFacade(core),
        emit_event=app.runner.event_log.emit,
    )


def test_default_assistant_exposes_all_functional_builtin_tools(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))

    expected = set(BUILTIN_TOOLSETS["coding"] + BUILTIN_TOOLSETS["demiurge_control"] + BUILTIN_TOOLSETS["schedule"])

    assert set(core.builtin_tool_names) == expected
    defaults = core.raw_manifest["capabilities"]["defaults"]
    assert {"fs.read", "fs.write", "terminal.exec", "task.control", "network.fetch", "schedule.manage"}.issubset(defaults)


@pytest.mark.asyncio
async def test_schedule_manage_creates_lists_updates_disables_enables_and_deletes_schedule(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake", timezone="Asia/Shanghai")
    app.approval_runtime.provider = StaticApprovalProvider("allow")
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    schedule_dir = app.version_store.active_core_path("assistant") / "agent" / "schedules"

    created = await _execute(
        app,
        core,
        "schedule_manage",
        {"action": "create", "schedule": "0 9 * * *", "prompt": "Write a daily summary."},
    )
    schedule_id = created.data["schedule"]["schedule_id"]
    schedule_path = schedule_dir / f"{schedule_id}.yaml"

    assert created.is_error is False
    assert schedule_path.exists()
    assert yaml.safe_load(schedule_path.read_text(encoding="utf-8")) == {
        "enabled": True,
        "schedule": "0 9 * * *",
        "prompt": "Write a daily summary.",
        "modules": {"input": ["base_input"], "output": ["base_output"]},
        "delivery": {"mode": "local"},
    }
    assert created.data["runtime_timezone"] == "Asia/Shanghai"
    assert created.data["runtime_timezone_source"] == "cli"

    listed = await _execute(app, core, "schedule_manage", {"action": "list"})
    assert listed.is_error is False
    assert [item["schedule_id"] for item in listed.data["schedules"]] == [schedule_id]
    assert listed.data["runtime_timezone"] == "Asia/Shanghai"

    raw = yaml.safe_load(schedule_path.read_text(encoding="utf-8"))
    raw["delivery"] = {"mode": "local"}
    raw["modules"] = {"input": ["base_input"], "output": ["base_output"]}
    schedule_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    updated = await _execute(
        app,
        core,
        "schedule_manage",
        {
            "action": "update",
            "schedule_id": schedule_id,
            "schedule": "30 10 * * *",
            "prompt": "Write an updated summary.",
        },
    )
    updated_raw = yaml.safe_load(schedule_path.read_text(encoding="utf-8"))

    assert updated.is_error is False
    assert updated_raw["schedule"] == "30 10 * * *"
    assert updated_raw["prompt"] == "Write an updated summary."
    assert updated_raw["delivery"] == {"mode": "local"}
    assert updated_raw["modules"] == {"input": ["base_input"], "output": ["base_output"]}

    disabled = await _execute(app, core, "schedule_manage", {"action": "disable", "schedule_id": schedule_id})
    assert disabled.is_error is False
    assert yaml.safe_load(schedule_path.read_text(encoding="utf-8"))["enabled"] is False

    enabled = await _execute(app, core, "schedule_manage", {"action": "enable", "schedule_id": schedule_id})
    assert enabled.is_error is False
    assert yaml.safe_load(schedule_path.read_text(encoding="utf-8"))["enabled"] is True

    deleted = await _execute(app, core, "schedule_manage", {"action": "delete", "schedule_id": schedule_id})
    assert deleted.is_error is False
    assert not schedule_path.exists()


@pytest.mark.asyncio
async def test_schedule_manage_rejects_invalid_cron_id_and_duplicates(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    app.approval_runtime.provider = StaticApprovalProvider("allow")
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))

    invalid_cron = await _execute(
        app,
        core,
        "schedule_manage",
        {
            "action": "create",
            "schedule_id": "daily",
            "schedule": "not cron",
            "prompt": "Bad schedule",
        },
    )
    invalid_id = await _execute(
        app,
        core,
        "schedule_manage",
        {
            "action": "create",
            "schedule_id": "../daily",
            "schedule": "0 9 * * *",
            "prompt": "Bad id",
        },
    )
    first = await _execute(
        app,
        core,
        "schedule_manage",
        {
            "action": "create",
            "schedule_id": "daily",
            "schedule": "0 9 * * *",
            "prompt": "Daily",
        },
    )
    duplicate = await _execute(
        app,
        core,
        "schedule_manage",
        {
            "action": "create",
            "schedule_id": "daily",
            "schedule": "0 10 * * *",
            "prompt": "Duplicate",
        },
    )

    assert invalid_cron.is_error is True
    assert "invalid cron expression" in invalid_cron.content
    assert invalid_id.is_error is True
    assert "schedule_id must" in invalid_id.content
    assert first.is_error is False
    assert duplicate.is_error is True
    assert "already exists" in duplicate.content


@pytest.mark.asyncio
async def test_schedule_manage_write_requires_capability_and_approval(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    manifest_path = app.version_store.active_core_path("assistant") / "agent.yaml"
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    raw["capabilities"]["defaults"].pop("schedule.manage")
    manifest_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))

    denied_by_capability = await _execute(
        app,
        core,
        "schedule_manage",
        {
            "action": "create",
            "schedule_id": "daily",
            "schedule": "0 9 * * *",
            "prompt": "Daily",
        },
    )

    assert denied_by_capability.is_error is True
    assert "capability denied" in denied_by_capability.content

    raw["capabilities"]["defaults"]["schedule.manage"] = {"scope": "core"}
    manifest_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    app.approval_runtime.provider = StaticApprovalProvider("deny")

    denied_by_approval = await _execute(
        app,
        core,
        "schedule_manage",
        {
            "action": "create",
            "schedule_id": "daily",
            "schedule": "0 9 * * *",
            "prompt": "Daily",
        },
    )

    assert denied_by_approval.is_error is True
    assert denied_by_approval.data["executionStarted"] is False
    assert not (app.version_store.active_core_path("assistant") / "agent" / "schedules" / "daily.yaml").exists()


@pytest.mark.asyncio
async def test_schedule_manage_list_does_not_require_schedule_manage_capability(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    manifest_path = app.version_store.active_core_path("assistant") / "agent.yaml"
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    raw["capabilities"]["defaults"].pop("schedule.manage")
    manifest_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))

    result = await _execute(app, core, "schedule_manage", {"action": "list"})

    assert result.is_error is False
    assert result.data["success"] is True
    assert result.data["count"] == 0
    assert result.data["schedules"] == []
    assert "runtime_timezone" in result.data
    assert "runtime_timezone_source" in result.data


@pytest.mark.asyncio
async def test_readonly_file_tools_auto_approve_inside_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "note.txt").write_text("alpha\nneedle\nomega", encoding="utf-8")
    app = create_app(home=tmp_path / "home", provider_name="fake", workspace=workspace)
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))

    listed = await _execute(app, core, "search_files", {"path": ".", "target": "name", "query": "note"})
    read = await _execute(app, core, "read_file", {"path": "note.txt", "offset": 0, "limit": 5})
    searched = await _execute(app, core, "search_files", {"query": "needle", "path": "."})

    assert listed.is_error is False
    assert "note.txt" in listed.content
    assert read.content.startswith("alpha")
    assert "truncated" in read.content
    assert "note.txt:2" in searched.content
    events = [event["type"] for event in app.runner.event_log.tail(20)]
    assert "approval.decided" in events
    assert "approval.requested" not in events


@pytest.mark.asyncio
async def test_search_files_name_and_content_modes_shape_output(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "alpha.txt").write_text("needle\n", encoding="utf-8")
    (workspace / "beta.md").write_text("other\nneedle again\n", encoding="utf-8")
    app = create_app(home=tmp_path / "home", provider_name="fake", workspace=workspace)
    core = _load_core_with(app, capabilities={"fs.read": {"scope": "workspace"}})

    names = await _execute(app, core, "search_files", {"target": "name", "query": "alpha", "pattern": "*.txt"})
    content = await _execute(app, core, "search_files", {"target": "content", "query": "needle", "path": ".", "pattern": "*.*"})

    assert names.is_error is False
    assert "alpha.txt" in names.content
    assert "beta.md" not in names.content
    assert content.is_error is False
    assert "alpha.txt:1" in content.content
    assert content.data["matches"][0]["path"] == "alpha.txt"


@pytest.mark.asyncio
async def test_sensitive_read_requires_approval_and_can_be_denied(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / ".env").write_text("TOKEN=secret", encoding="utf-8")
    app = create_app(home=tmp_path / "home", provider_name="fake", workspace=workspace)
    app.approval_runtime.provider = StaticApprovalProvider("deny")
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))

    result = await _execute(app, core, "read_file", {"path": ".env"})

    assert result.is_error is True
    assert result.data["executionStarted"] is False
    events = [event["type"] for event in app.runner.event_log.tail(20)]
    assert "approval.requested" in events
    assert "approval.denied" in events


@pytest.mark.asyncio
async def test_write_and_patch_tools_with_approval(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = create_app(home=tmp_path / "home", provider_name="fake", workspace=workspace)
    app.approval_runtime.provider = StaticApprovalProvider("allow")
    core = _load_core_with(app, capabilities={"fs.write": {"scope": "workspace"}})

    write = await _execute(app, core, "write_file", {"path": "notes/a.txt", "content": "hello"})
    patch = await _execute(app, core, "patch", {"path": "notes/a.txt", "old": "hello", "new": "there"})

    assert all(not item.is_error for item in [write, patch])
    assert (workspace / "notes/a.txt").read_text(encoding="utf-8") == "there"
    assert "-hello" in patch.content
    assert "+there" in patch.content


@pytest.mark.asyncio
async def test_write_tool_denial_does_not_create_file(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = create_app(home=tmp_path / "home", provider_name="fake", workspace=workspace)
    app.approval_runtime.provider = StaticApprovalProvider("deny")
    core = _load_core_with(app, capabilities={"fs.write": {"scope": "workspace"}})

    result = await _execute(app, core, "write_file", {"path": "blocked.txt", "content": "nope"})

    assert result.is_error is True
    assert result.data["executionStarted"] is False
    assert not (workspace / "blocked.txt").exists()


@pytest.mark.asyncio
async def test_workspace_escape_is_rejected_before_execution(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = create_app(home=tmp_path / "home", provider_name="fake", workspace=workspace)
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))

    result = await _execute(app, core, "read_file", {"path": "../outside.txt"})

    assert result.is_error is True
    assert result.data["executionStarted"] is False


@pytest.mark.asyncio
async def test_terminal_command_success_denial_timeout_and_cwd_scope(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "denied.txt").write_text("keep", encoding="utf-8")
    app = create_app(home=tmp_path / "home", provider_name="fake", workspace=workspace)
    app.approval_runtime.provider = StaticApprovalProvider("allow")
    core = _load_core_with(app, capabilities={"terminal.exec": {"scope": "workspace"}})

    success = await _execute(app, core, "terminal", {"command": "printf hello", "cwd": "."})
    timeout = await _execute(app, core, "terminal", {"command": "sleep 2", "timeout_seconds": 1})
    escape = await _execute(app, core, "terminal", {"command": "pwd", "cwd": ".."})

    app.approval_runtime.provider = StaticApprovalProvider("deny")
    denied = await _execute(app, core, "terminal", {"command": "rm denied.txt"})

    assert success.is_error is False
    assert "hello" in success.content
    assert timeout.is_error is True
    assert timeout.data["timed_out"] is True
    assert escape.is_error is True
    assert escape.data["executionStarted"] is False
    assert denied.is_error is True
    assert denied.data["executionStarted"] is False
    assert (workspace / "denied.txt").exists()


def test_windows_terminal_printf_compat_formats_common_smoke_command():
    assert tool_runtime._format_windows_printf("%s\\n", ["hello"]) == "hello\n"

    translated = tool_runtime._windows_posix_compat_command("printf '%s\\n' hello")

    assert translated is not None
    assert "_format_windows_printf" in translated
    assert "printf" not in translated.split(" -c ", 1)[0]


@pytest.mark.asyncio
async def test_windows_terminal_executes_compat_command_but_approves_original(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "target.txt").write_text("delete me", encoding="utf-8")
    app = create_app(home=tmp_path / "home", provider_name="fake", workspace=workspace)
    approval_provider = RecordingApprovalProvider(["allow"])
    app.approval_runtime.provider = approval_provider
    core = _load_core_with(app, capabilities={"terminal.exec": {"scope": "workspace"}})
    captured = {}

    class Completed:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(command, *, cwd, env, shell, text, stdout, stderr, timeout, check):
        captured["command"] = command
        return Completed()

    monkeypatch.setattr(
        "demiurge.tools.runtime._terminal_execution_command",
        lambda command: tool_runtime._windows_posix_compat_command(command) or command,
    )
    monkeypatch.setattr("demiurge.tools.runtime.subprocess.run", fake_run)

    result = await _execute(app, core, "terminal", {"command": "rm target.txt", "cwd": "."})

    assert result.is_error is False
    assert "os.remove" in captured["command"]
    assert "rm target.txt" not in captured["command"]
    assert approval_provider.requests[0].command == "rm target.txt"
    assert approval_provider.requests[0].arguments_preview["command"] == "rm target.txt"


@pytest.mark.asyncio
async def test_terminal_injects_runtime_timezone_env(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("DEMIURGE_TIMEZONE", "Europe/Paris")
    app = create_app(home=tmp_path / "home", provider_name="fake", workspace=workspace, timezone="Asia/Shanghai")
    core = _load_core_with(app, capabilities={"terminal.exec": {"scope": "workspace"}})
    captured = {}

    class Completed:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def fake_run(command, *, cwd, env, shell, text, stdout, stderr, timeout, check):
        captured["env"] = env
        return Completed()

    monkeypatch.setattr("demiurge.tools.runtime.subprocess.run", fake_run)

    result = await _execute(app, core, "terminal", {"command": "printf ok", "cwd": "."})

    assert result.is_error is False
    assert captured["env"]["TZ"] == "Asia/Shanghai"
    assert "DEMIURGE_TIMEZONE" not in captured["env"]


@pytest.mark.asyncio
async def test_safe_terminal_command_auto_approves_without_prompt(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = create_app(home=tmp_path / "home", provider_name="fake", workspace=workspace)
    app.approval_runtime.provider = StaticApprovalProvider("deny")
    core = _load_core_with(app, capabilities={"terminal.exec": {"scope": "workspace"}})

    result = await _execute(app, core, "terminal", {"command": "printf safe"})

    assert result.is_error is False
    assert "safe" in result.content
    events = app.runner.event_log.tail(20)
    approval_events = [event for event in events if event["type"].startswith("approval.")]
    assert [event["type"] for event in approval_events] == ["approval.decided"]
    assert approval_events[0]["automatic"] is True
    assert approval_events[0]["risk"] == "low"


@pytest.mark.asyncio
async def test_global_terminal_deny_blocks_safe_commands(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    home = tmp_path / "home"
    app = create_app(home=home, provider_name="fake", workspace=workspace)
    fallback = home / "agents" / "agent.yaml"
    raw_fallback = yaml.safe_load(fallback.read_text(encoding="utf-8"))
    raw_fallback["approval"] = {"tools": {"terminal": "deny"}}
    fallback.write_text(yaml.safe_dump(raw_fallback, sort_keys=False), encoding="utf-8")
    app = create_app(home=home, provider_name="fake", workspace=workspace)
    app.approval_runtime.provider = StaticApprovalProvider("allow")
    core = _load_core_with(app, capabilities={"terminal.exec": {"scope": "workspace"}})

    result = await _execute(app, core, "terminal", {"command": "printf no"})

    assert result.is_error is True
    assert result.data["executionStarted"] is False
    assert result.data["approval"]["value"] == "deny"


@pytest.mark.asyncio
async def test_terminal_background_task_can_be_listed_and_waited(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = create_app(home=tmp_path / "home", provider_name="fake", workspace=workspace)
    app.approval_runtime.provider = StaticApprovalProvider("deny")
    core = _load_core_with(app, capabilities={"terminal.exec": {"scope": "workspace"}, "task.control": {}})

    started = await _execute(app, core, "terminal", {"command": "printf ready", "background": True})
    task_id = started.data["task_id"]
    waited = await app.task_worker.wait(task_id, timeout_seconds=5)
    log = app.task_worker.log(task_id)
    listed = await _execute(app, core, "task_list", {"kind": "terminal.exec"})

    assert started.is_error is False
    assert set(started.data) == {"task_id"}
    assert waited.running is False
    assert any("ready" in line for line in log)
    assert task_id in listed.content
    task = app.control_plane.read(task_id, view="debug")
    assert task["kind"] == "terminal.exec"
    assert task["status"] == "succeeded"
    assert task["notify_policy"] == "completion_event"
    assert any("ready" in line["text"] for line in task["logs"])


@pytest.mark.asyncio
async def test_terminal_background_task_notifies(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = create_app(home=tmp_path / "home", provider_name="fake", workspace=workspace)
    app.approval_runtime.provider = StaticApprovalProvider("deny")
    core = _load_core_with(app, capabilities={"terminal.exec": {"scope": "workspace"}, "task.control": {}})

    started = await _execute(app, core, "terminal", {"command": "printf task-ready", "background": True})
    task_id = started.data["task_id"]
    waited = await app.task_worker.wait(task_id, timeout_seconds=5)
    log = app.task_worker.log(task_id)
    listed = await _execute(app, core, "task_list", {"kind": "terminal.exec"})

    assert waited.status == "succeeded"
    assert set(started.data) == {"task_id"}
    assert waited.running is False
    assert any("task-ready" in line for line in log)
    assert task_id in listed.content
    events = app.task_worker.pending_events_for_session("session_test")
    assert [event.task_id for event in events] == [task_id]
    task = app.control_plane.read(task_id, view="debug")
    assert task["kind"] == "terminal.exec"
    assert task["status"] == "succeeded"
    assert any("task-ready" in line["text"] for line in task["logs"])


@pytest.mark.asyncio
async def test_task_list_is_scoped_to_current_session(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    core = _load_core_with(app, capabilities={"task.control": {}})

    async def quick_task(ctx):
        return {"summary": f"done {ctx.task_id}"}

    current = app.task_worker.start_task(
        kind="terminal.exec",
        owner_session_id="session_test",
        owner_turn_id="turn_test",
        source_tool="terminal",
        task_factory=quick_task,
    )
    other = app.task_worker.start_task(
        kind="terminal.exec",
        owner_session_id="other_session",
        owner_turn_id="other_turn",
        source_tool="terminal",
        task_factory=quick_task,
    )
    await app.task_worker.wait(current.task_id, timeout_seconds=5)
    await app.task_worker.wait(other.task_id, timeout_seconds=5)

    listed = await _execute(app, core, "task_list", {"kind": "terminal.exec"})

    assert current.task_id in listed.content
    assert other.task_id not in listed.content


def test_task_list_schema_does_not_expose_owner_session_id(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))

    task_list = next(tool for tool in app.tool_runtime.definitions_for(core) if tool.name == "task_list")

    assert set(task_list.input_schema["properties"]) == {"kind", "include_completed"}
    assert "owner_session_id" not in json.dumps(task_list.input_schema)


def test_skill_manage_schema_exposes_file_level_actions():
    schema = BUILTIN_TOOL_DEFINITIONS["skill_manage"].input_schema

    assert set(schema["properties"]["action"]["enum"]) == {"create", "update", "delete", "patch", "write_file", "remove_file"}
    assert {"file_path", "file_content", "old_string", "new_string", "replace_all"}.issubset(schema["properties"])


@pytest.mark.asyncio
async def test_evolve_core_background_creates_candidate_without_promoting(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    before = app.version_store.active_pointer("assistant").active_revision

    class EditingEvolver:
        async def run(self, *, target_core_path, **kwargs):
            soul = target_core_path / "agent" / "SOUL.md"
            soul.write_text(soul.read_text(encoding="utf-8") + "\n\nBackground evolve edit.\n", encoding="utf-8")
            return EvolverRunResult(summary="edited candidate", session_id="child_session", turn_id="child_turn")

    app.evolution_runtime.evolver_runner = EditingEvolver()

    started = await _execute(app, core, "evolve_core", {"goal": "edit soul in background", "background": True})
    task_id = started.data["task_id"]
    waited = await app.task_worker.wait(task_id, timeout_seconds=5)
    waited_payload = waited.to_payload(include_log=True, log=app.task_worker.log(task_id))

    assert set(started.data) == {"task_id"}
    assert waited.status == "succeeded"
    assert waited_payload["metadata"]["promoted"] is False
    assert waited_payload["metadata"]["new_revision"] is None
    assert "ready for review" in waited_payload["summary"]
    assert app.version_store.active_pointer("assistant").active_revision == before
    candidate_soul = Path(waited_payload["metadata"]["agents_root"]) / "assistant" / "agent" / "SOUL.md"
    assert "Background evolve edit." in candidate_soul.read_text(encoding="utf-8")
    task = app.control_plane.read(task_id, view="debug")
    assert task["kind"] == "evolver.run"
    assert task["status"] == "succeeded"
    assert task["result_ref"] == waited_payload["result_ref"]


@pytest.mark.asyncio
async def test_rollback_core_returns_error_when_live_tree_has_local_edits(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    soul = app.version_store.active_core_path("assistant") / "agent" / "SOUL.md"
    soul.write_text(soul.read_text(encoding="utf-8") + "\n\nCommitted setup edit.\n", encoding="utf-8")
    app.version_store.core_repository.commit_live(reason="test setup", summary="test setup")
    soul.write_text(soul.read_text(encoding="utf-8") + "\n\nDirty rollback edit.\n", encoding="utf-8")
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    before = app.version_store.core_repository.live_revision()

    result = await _execute(app, core, "rollback_core", {})

    assert result.is_error is True
    assert "local agent edits must be saved or discarded" in result.content
    assert app.version_store.core_repository.live_revision() == before


@pytest.mark.asyncio
async def test_terminal_dangerous_session_approval_caches_by_rule(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    first = workspace / "first.txt"
    second = workspace / "second.txt"
    first.write_text("first", encoding="utf-8")
    second.write_text("second", encoding="utf-8")
    app = create_app(home=tmp_path / "home", provider_name="fake", workspace=workspace)
    provider = RecordingApprovalProvider(["always_allow_for_session", "deny"])
    app.approval_runtime.provider = provider
    core = _load_core_with(app, capabilities={"terminal.exec": {"scope": "workspace"}})

    first_result = await _execute(app, core, "terminal", {"command": "rm first.txt"})
    second_result = await _execute(app, core, "terminal", {"command": "rm second.txt"})

    assert first_result.data["executionStarted"] is True
    assert second_result.data["executionStarted"] is True
    assert not first.exists()
    assert not second.exists()
    assert len(provider.requests) == 1
    assert provider.requests[0].cache_key == "terminal:terminal.exec:file-delete"


@pytest.mark.asyncio
async def test_terminal_hardline_blocks_before_global_auto(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    home = tmp_path / "home"
    app = create_app(home=home, provider_name="fake", workspace=workspace)
    fallback = home / "agents" / "agent.yaml"
    raw_fallback = yaml.safe_load(fallback.read_text(encoding="utf-8"))
    raw_fallback["approval"] = {"tools": {"terminal": "auto"}}
    fallback.write_text(yaml.safe_dump(raw_fallback, sort_keys=False), encoding="utf-8")
    app = create_app(home=home, provider_name="fake", workspace=workspace)
    app.approval_runtime.provider = StaticApprovalProvider("allow")
    core = _load_core_with(app, capabilities={"terminal.exec": {"scope": "workspace"}})

    result = await _execute(app, core, "terminal", {"command": "rm -rf /"})

    assert result.is_error is True
    assert result.data["executionStarted"] is False
    assert result.data["command_guard"]["action"] == "block"


@pytest.mark.asyncio
async def test_terminal_promptable_commands_require_approval(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = create_app(home=tmp_path / "home", provider_name="fake", workspace=workspace)
    app.approval_runtime.provider = StaticApprovalProvider("deny")
    core = _load_core_with(app, capabilities={"terminal.exec": {"scope": "workspace"}})

    package_install = await _execute(app, core, "terminal", {"command": "npm install"})
    redirection = await _execute(app, core, "terminal", {"command": "printf hello > out.txt"})
    complex_shell = await _execute(app, core, "terminal", {"command": "python <<'PY'\nprint('hello')\nPY"})

    assert package_install.is_error is True
    assert package_install.data["executionStarted"] is False
    assert redirection.is_error is True
    assert redirection.data["executionStarted"] is False
    assert not (workspace / "out.txt").exists()
    assert complex_shell.is_error is True
    assert complex_shell.data["executionStarted"] is False


@pytest.mark.asyncio
async def test_todo_tool_persists_per_session(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    core = _load_core_with(app, capabilities={})

    added = await _execute(app, core, "todo", {"action": "add", "text": "ship docs"})
    listed = await _execute(app, core, "todo", {"action": "list"})
    completed = await _execute(app, core, "todo", {"action": "complete", "index": 1})

    assert added.is_error is False
    assert "ship docs" in listed.content
    assert completed.data["todos"][0]["done"] is True


@pytest.mark.asyncio
async def test_skill_manage_creates_updates_and_deletes_runtime_skill(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    app.approval_runtime.provider = StaticApprovalProvider("allow")
    core = _load_core_with(app, capabilities={"fs.write": {"scope": "workspace"}})

    created = await _execute(
        app,
        core,
        "skill_manage",
        {"action": "create", "name": "local-note", "content": "---\ndescription: local\n---\n\n# Local\n"},
    )
    skill_path = app.version_store.active_core_path("assistant") / "agent/skills/local-note/SKILL.md"
    assert created.is_error is False
    assert created.data["success"] is True
    assert created.data["changed"] is True
    assert created.data["effective_next_turn"] is True
    assert created.data["path"] == "agent/skills/local-note/SKILL.md"
    assert skill_path.exists()

    updated = await _execute(
        app,
        core,
        "skill_manage",
        {"action": "update", "name": "local-note", "content": "---\ndescription: updated\n---\n\n# Updated\n"},
    )
    assert updated.is_error is False
    assert "# Updated" in skill_path.read_text(encoding="utf-8")

    reloaded = app.core_loader.load(app.version_store.active_core_path("assistant"))
    deleted = await _execute(app, reloaded, "skill_manage", {"action": "delete", "name": "local-note"})

    assert deleted.is_error is False
    assert not skill_path.exists()


@pytest.mark.asyncio
async def test_skill_manage_patches_skill_and_support_files(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    app.approval_runtime.provider = StaticApprovalProvider("allow")
    core = _load_core_with(app, capabilities={"fs.write": {"scope": "workspace"}})

    created = await _execute(
        app,
        core,
        "skill_manage",
        {"action": "create", "name": "local-note", "content": "---\ndescription: local\n---\n\n# Local\n\nOriginal step.\n"},
    )
    patched = await _execute(
        app,
        core,
        "skill_manage",
        {"action": "patch", "name": "local-note", "old_string": "Original step.", "new_string": "Patched step."},
    )
    wrote_reference = await _execute(
        app,
        core,
        "skill_manage",
        {
            "action": "write_file",
            "name": "local-note",
            "file_path": "references/checklist.md",
            "file_content": "# Checklist\n\n- Verify patch.\n",
        },
    )
    patched_reference = await _execute(
        app,
        core,
        "skill_manage",
        {
            "action": "patch",
            "name": "local-note",
            "file_path": "references/checklist.md",
            "old_string": "Verify patch.",
            "new_string": "Verify support patch.",
        },
    )
    reloaded = app.core_loader.load(app.version_store.active_core_path("assistant"))
    viewed_reference = await _execute(
        app,
        reloaded,
        "skill_view",
        {"name": "local-note", "file_path": "references/checklist.md"},
    )

    assert created.is_error is False
    assert patched.is_error is False
    assert "Patched step." in (app.version_store.active_core_path("assistant") / "agent/skills/local-note/SKILL.md").read_text(encoding="utf-8")
    assert "---" in patched.data["diff"]
    assert wrote_reference.is_error is False
    assert wrote_reference.data["path"] == "agent/skills/local-note/references/checklist.md"
    assert patched_reference.is_error is False
    assert "Verify support patch." in viewed_reference.data["content"]


@pytest.mark.asyncio
async def test_skill_manage_removes_support_files(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    app.approval_runtime.provider = StaticApprovalProvider("allow")
    core = _load_core_with(app, capabilities={"fs.write": {"scope": "workspace"}})

    await _execute(
        app,
        core,
        "skill_manage",
        {"action": "create", "name": "local-note", "content": "---\ndescription: local\n---\n\n# Local\n"},
    )
    await _execute(
        app,
        core,
        "skill_manage",
        {
            "action": "write_file",
            "name": "local-note",
            "file_path": "references/checklist.md",
            "file_content": "# Checklist\n",
        },
    )
    removed = await _execute(
        app,
        core,
        "skill_manage",
        {"action": "remove_file", "name": "local-note", "file_path": "references/checklist.md"},
    )
    reloaded = app.core_loader.load(app.version_store.active_core_path("assistant"))

    assert removed.is_error is False
    assert removed.data["changed"] is True
    assert reloaded.skill_by_id("local-note").linked_files == {}


@pytest.mark.asyncio
async def test_skill_manage_rejects_unsafe_support_paths(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    app.approval_runtime.provider = StaticApprovalProvider("allow")
    core = _load_core_with(app, capabilities={"fs.write": {"scope": "workspace"}})
    await _execute(
        app,
        core,
        "skill_manage",
        {"action": "create", "name": "local-note", "content": "---\ndescription: local\n---\n\n# Local\n"},
    )

    parent_escape = await _execute(
        app,
        core,
        "skill_manage",
        {"action": "write_file", "name": "local-note", "file_path": "../secret.md", "file_content": "secret"},
    )
    absolute = await _execute(
        app,
        core,
        "skill_manage",
        {"action": "write_file", "name": "local-note", "file_path": "/tmp/secret.md", "file_content": "secret"},
    )
    unapproved_dir = await _execute(
        app,
        core,
        "skill_manage",
        {"action": "write_file", "name": "local-note", "file_path": "notes/secret.md", "file_content": "secret"},
    )
    hidden_path = await _execute(
        app,
        core,
        "skill_manage",
        {"action": "write_file", "name": "local-note", "file_path": "references/.secret.md", "file_content": "secret"},
    )

    assert parent_escape.is_error is True
    assert absolute.is_error is True
    assert unapproved_dir.is_error is True
    assert hidden_path.is_error is True


@pytest.mark.asyncio
async def test_skill_manage_rejects_symlink_escape(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    app.approval_runtime.provider = StaticApprovalProvider("allow")
    core = _load_core_with(app, capabilities={"fs.write": {"scope": "workspace"}})
    await _execute(
        app,
        core,
        "skill_manage",
        {"action": "create", "name": "local-note", "content": "---\ndescription: local\n---\n\n# Local\n"},
    )
    skill_dir = app.version_store.active_core_path("assistant") / "agent/skills/local-note"
    outside = tmp_path / "outside"
    outside.mkdir()
    (skill_dir / "references").symlink_to(outside)

    result = await _execute(
        app,
        core,
        "skill_manage",
        {"action": "write_file", "name": "local-note", "file_path": "references/secret.md", "file_content": "secret"},
    )

    assert result.is_error is True
    assert not (outside / "secret.md").exists()


@pytest.mark.asyncio
async def test_skill_manage_rolls_back_when_core_load_fails(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    app.approval_runtime.provider = StaticApprovalProvider("allow")
    core = _load_core_with(app, capabilities={"fs.write": {"scope": "workspace"}})
    await _execute(
        app,
        core,
        "skill_manage",
        {"action": "create", "name": "local-note", "content": "---\ndescription: local\n---\n\n# Local\n"},
    )
    skill_path = app.version_store.active_core_path("assistant") / "agent/skills/local-note/SKILL.md"
    before = skill_path.read_text(encoding="utf-8")

    result = await _execute(
        app,
        core,
        "skill_manage",
        {"action": "update", "name": "local-note", "content": "---\ndescription: [\n---\n\n# Broken\n"},
    )

    assert result.is_error is True
    assert "rolled back" in result.content
    assert skill_path.read_text(encoding="utf-8") == before


@pytest.mark.asyncio
async def test_skill_manage_uses_configured_skills_root(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    app.approval_runtime.provider = StaticApprovalProvider("allow")
    manifest_path = app.version_store.active_core_path("assistant") / "agent.yaml"
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    raw.setdefault("slots", {})["skills"] = "custom/skills"
    raw.setdefault("capabilities", {}).setdefault("defaults", {})["fs.write"] = {"scope": "workspace"}
    manifest_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))

    result = await _execute(
        app,
        core,
        "skill_manage",
        {"action": "create", "name": "local-note", "content": "---\ndescription: local\n---\n\n# Local\n"},
    )

    assert result.is_error is False
    assert result.data["path"] == "custom/skills/local-note/SKILL.md"
    assert (app.version_store.active_core_path("assistant") / "custom/skills/local-note/SKILL.md").exists()
    assert not (app.version_store.active_core_path("assistant") / "agent/skills/local-note/SKILL.md").exists()


@pytest.mark.asyncio
async def test_session_search_reads_existing_messages_without_history_write(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    await app.runner.run_turn("alpha search target")
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    before = app.session_runtime.message_count(app.runner.session_id)

    result = await _execute(app, core, "session_search", {"query": "alpha", "limit": 5})
    after = app.session_runtime.message_count(app.runner.session_id)

    assert result.is_error is False
    assert "alpha search target" in result.content
    assert after == before


@pytest.mark.asyncio
async def test_web_extract_requires_approval_and_truncates(tmp_path, monkeypatch):
    class Headers:
        def get_content_charset(self):
            return "utf-8"

        def get(self, name):
            return "text/plain" if name == "content-type" else None

    class Response:
        status = 200
        headers = Headers()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self, size):
            return ("x" * 100).encode("utf-8")[:size]

    def fake_urlopen(request, timeout):
        return Response()

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = create_app(home=tmp_path / "home", provider_name="fake", workspace=workspace)
    core = _load_core_with(app, capabilities={"network.fetch": {"requires_approval": True}})

    app.approval_runtime.provider = StaticApprovalProvider("deny")
    denied = await _execute(app, core, "web_extract", {"url": "https://example.com"})

    app.approval_runtime.provider = StaticApprovalProvider("allow")
    monkeypatch.setattr("demiurge.tools.runtime.urllib.request.urlopen", fake_urlopen)
    fetched = await _execute(app, core, "web_extract", {"url": "https://example.com", "max_chars": 20})

    assert denied.is_error is True
    assert denied.data["executionStarted"] is False
    assert fetched.is_error is False
    assert fetched.content == "x" * 20
    assert fetched.model_output == "x" * 20
    assert fetched.data["truncated"] is True


@pytest.mark.asyncio
async def test_removed_tool_names_are_not_aliases(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    core = _load_core_with(app, capabilities={})

    removed = [
        "list_dir",
        "glob",
        "grep",
        "append_file",
        "patch_file",
        "mkdir",
        "delete_path",
        "run_command",
        "ask_question",
        "web_fetch",
        "web_search",
        "process",
        "jo" + "b",
    ]
    results = [await _execute(app, core, name, {}) for name in removed]

    assert all(result.is_error for result in results)
    assert all("tool not found" in result.content for result in results)


@pytest.mark.asyncio
async def test_tools_list_returns_registry_metadata(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))

    result = await _execute(app, core, "tools_list", {})

    assert result.is_error is False
    payload = json.loads(result.content)
    assert payload["success"] is True
    names = {tool["name"] for tool in payload["tools"]}
    assert {"tools_list", "task_list", "skills_list", "read_file", "write_file", "patch", "terminal", "echo"}.issubset(names)
    assert {"jo" + "b", "process"}.isdisjoint(names)
    read_file = next(tool for tool in payload["tools"] if tool["name"] == "read_file")
    assert read_file["source"] == "builtin"
    assert read_file["capability"] == "fs.read"
    terminal = next(tool for tool in payload["tools"] if tool["name"] == "terminal")
    assert terminal["capability"] == "terminal.exec"
    assert terminal["approval_policy"] == "prompt"
    model_payload = json.loads(result.model_output or "")
    model_terminal = next(tool for tool in model_payload["tools"] if tool["name"] == "terminal")
    assert model_terminal == {
        "name": "terminal",
        "description": terminal["description"],
        "source": "builtin",
        "enabled": True,
    }
    assert "api_key" not in result.content
    assert "approval_policy" not in result.model_output
    assert "capability" not in result.model_output
    assert "risk" not in result.model_output


def test_builtin_tool_descriptions_are_model_facing_not_approval_prompts():
    prompt_text = "\n".join(definition.description.lower() for definition in BUILTIN_TOOL_DEFINITIONS.values())

    assert "after approval" not in prompt_text
    assert "requires approval" not in prompt_text
    assert "need approval" not in prompt_text


@pytest.mark.asyncio
async def test_core_approval_config_cannot_lower_host_safety_baseline(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "blocked.txt").write_text("blocked", encoding="utf-8")
    app = create_app(home=tmp_path / "home", provider_name="fake", workspace=workspace)
    app.approval_runtime.provider = StaticApprovalProvider("deny")
    manifest_path = app.version_store.active_core_path("assistant") / "agent.yaml"
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    raw["approval"] = {"tools": {"terminal": "auto"}}
    manifest_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))

    result = await _execute(app, core, "terminal", {"command": "rm blocked.txt"})

    assert result.is_error is True
    assert result.data["executionStarted"] is False
    assert result.data["approval"]["value"] == "deny"
    assert (workspace / "blocked.txt").exists()


@pytest.mark.asyncio
async def test_global_approval_config_can_auto_allow_prompt_tools(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    doomed = workspace / "doomed.txt"
    doomed.write_text("delete me", encoding="utf-8")
    home = tmp_path / "home"
    app = create_app(home=home, provider_name="fake", workspace=workspace)
    fallback = home / "agents" / "agent.yaml"
    raw_fallback = yaml.safe_load(fallback.read_text(encoding="utf-8"))
    raw_fallback["approval"] = {"tools": {"terminal": "auto"}}
    fallback.write_text(yaml.safe_dump(raw_fallback, sort_keys=False), encoding="utf-8")
    app = create_app(home=home, provider_name="fake", workspace=workspace)
    app.approval_runtime.provider = StaticApprovalProvider("deny")
    core = _load_core_with(app, capabilities={"terminal.exec": {"scope": "workspace"}})

    result = await _execute(app, core, "terminal", {"command": "rm doomed.txt"})

    assert result.is_error is False
    assert result.data["executionStarted"] is True
    assert not doomed.exists()


@pytest.mark.asyncio
async def test_skills_list_returns_metadata_without_skill_body(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    _install_test_skills(app)
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))

    result = await _execute(app, core, "skills_list", {})

    assert result.is_error is False
    payload = json.loads(result.content)
    assert payload["success"] is True
    assert payload["count"] == 2
    assert payload["categories"] == ["development"]
    assert {skill["name"] for skill in payload["skills"]} == {"debugging", "project-notes"}
    assert payload["skills"][0]["description"]
    assert "Start from the exact failing command" not in result.content
    assert "Use this skill when" not in result.content
    assert result.model_output == result.content


@pytest.mark.asyncio
async def test_skills_list_filters_by_category(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    _install_test_skills(app)
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))

    development = await _execute(app, core, "skills_list", {"category": "development"})
    missing = await _execute(app, core, "skills_list", {"category": "missing"})

    assert development.data["count"] == 2
    assert missing.data["count"] == 0
    assert missing.data["categories"] == ["development"]


@pytest.mark.asyncio
async def test_skill_view_loads_main_document_into_model_output(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    _install_test_skills(app)
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))

    result = await _execute(app, core, "skill_view", {"name": "debugging"})

    assert result.is_error is False
    assert result.data["skill_id"] == "debugging"
    assert result.data["category"] == "development"
    assert result.data["linked_files"] == {"references": ["references/checklist.md"]}
    assert "# Debugging" in result.data["content"]
    assert "<skill name=\"debugging\"" in result.model_output
    assert "Start from the exact failing command" in result.model_output
    assert "references/checklist.md" in result.model_output


@pytest.mark.asyncio
async def test_skill_view_loads_packaged_linked_file(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    _install_test_skills(app)
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))

    result = await _execute(
        app,
        core,
        "skill_view",
        {"name": "debugging", "file_path": "references/checklist.md"},
    )

    assert result.is_error is False
    assert result.data["file_path"] == "references/checklist.md"
    assert "Debugging Checklist" in result.data["content"]
    assert "<skill_file name=\"debugging\"" in result.model_output
    assert "Debugging Checklist" in result.model_output


@pytest.mark.asyncio
async def test_skill_view_rejects_unknown_or_unlinked_files(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    _install_test_skills(app)
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))

    unknown_skill = await _execute(app, core, "skill_view", {"name": "missing"})
    absolute = await _execute(
        app,
        core,
        "skill_view",
        {"name": "debugging", "file_path": "/tmp/secret.txt"},
    )
    parent_escape = await _execute(
        app,
        core,
        "skill_view",
        {"name": "debugging", "file_path": "../debugging/SKILL.md"},
    )
    unlinked = await _execute(
        app,
        core,
        "skill_view",
        {"name": "debugging", "file_path": "SKILL.md"},
    )

    assert unknown_skill.is_error is True
    assert "skill not found" in unknown_skill.content
    assert absolute.is_error is True
    assert parent_escape.is_error is True
    assert unlinked.is_error is True
    assert "not allowed" in unlinked.content


@pytest.mark.asyncio
async def test_skill_view_rejects_linked_file_that_becomes_symlink_escape(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    _install_test_skills(app)
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    skill_file = app.version_store.active_core_path("assistant") / "agent/skills/debugging/references/checklist.md"
    outside = tmp_path / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    skill_file.unlink()
    skill_file.symlink_to(outside)

    result = await _execute(
        app,
        core,
        "skill_view",
        {"name": "debugging", "file_path": "references/checklist.md"},
    )

    assert result.is_error is True
    assert result.data["executionStarted"] is False


@pytest.mark.asyncio
async def test_missing_capability_denies_builtin_even_when_visible(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = create_app(home=tmp_path / "home", provider_name="fake", workspace=workspace)
    manifest_path = app.version_store.active_core_path("assistant") / "agent.yaml"
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    raw["capabilities"]["defaults"].pop("fs.write")
    manifest_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))

    result = await _execute(app, core, "write_file", {"path": "x.txt", "content": "x"})

    assert result.is_error is True
    assert "capability denied" in result.content
