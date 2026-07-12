import json
from dataclasses import dataclass
from pathlib import Path

import yaml
import pytest

from demiurge.app import create_app
from demiurge.evolution import EvolverRunResult
from demiurge.security.approval import ApprovalDecision, StaticApprovalProvider
from demiurge.security.capabilities import CapabilityFacade
from demiurge.core import BUILTIN_TOOLSETS
from demiurge.providers import ToolCall
from demiurge.runtime.scope import PrincipalScopeResolver
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


@dataclass
class FakeCoreMutationResult:
    summary: str = "fake mutation called"
    promoted: bool = True
    passed: bool = True


class FakeEvolutionAdapter:
    def __init__(self):
        self.start_calls = 0
        self.review_calls = 0
        self.promote_calls = 0
        self.discard_calls = 0

    async def start(self, **kwargs):
        self.start_calls += 1
        return FakeCoreMutationResult()

    async def review(self, run_id, *, target_core_id, goal):
        self.review_calls += 1
        return FakeCoreMutationResult()

    async def promote(self, run_id, *, target_core_id, reason):
        self.promote_calls += 1
        return FakeCoreMutationResult()

    def discard(self, run_id):
        self.discard_calls += 1
        return {"run_id": run_id}


@dataclass
class FakeRollbackPointer:
    active_revision: str = "fake-revision"


class FakeVersionStore:
    def __init__(self):
        self.rollback_calls = 0

    def rollback(self, core_id, *, target, reason):
        self.rollback_calls += 1
        return FakeRollbackPointer()


@dataclass
class FakeTaskRecord:
    task_id: str = "task_probe"


class FakeTaskWorker:
    def __init__(self):
        self.start_calls = 0

    def start_task(self, **kwargs):
        self.start_calls += 1
        return FakeTaskRecord()


def _set_test_home(monkeypatch: pytest.MonkeyPatch, home: Path) -> None:
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))


def _load_core_with(
    app,
    *,
    toolsets: list[str] | None = None,
    capabilities: dict[str, dict] | None = None,
    tool_metadata: dict[str, dict] | None = None,
):
    manifest_path = app.version_store.active_core_path("assistant") / "agent.yaml"
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if toolsets is not None:
        raw.setdefault("tools", {})["toolsets"] = toolsets
    defaults = raw.setdefault("capabilities", {}).setdefault("defaults", {})
    for capability, value in (capabilities or {}).items():
        defaults[capability] = value
    raw.setdefault("tools", {}).setdefault("metadata", {}).update(
        tool_metadata or {}
    )
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
    )


def _principal_scope(app, core, turn):
    resolver = PrincipalScopeResolver(app.runtime_store)
    if not app.runtime_store.session_owner_exists(turn.session_id):
        issued = resolver.local_operator(
            active_session_id=turn.session_id,
            reason="bind direct tool test session",
            allow_unowned_active=True,
        )
        app.session_runtime.create_session(
            session_id=turn.session_id,
            core_id=core.core_id,
            core_revision=core.revision,
            principal_scope=issued,
        )
    return resolver.origin_scope(session_id=turn.session_id)


def _conversation_scope(app, core, suffix):
    resolver = PrincipalScopeResolver(app.runtime_store)
    session_id = f"session_{suffix}"
    conversation_key = f"probe:conversation:{suffix}"
    principal_key = f"user_{suffix}"
    issued = resolver.issue_conversation(
        channel="probe",
        principal_key=principal_key,
        conversation_key=conversation_key,
        session_id=session_id,
    )
    app.session_runtime.create_session(
        session_id=session_id,
        core_id=core.core_id,
        core_revision=core.revision,
        channel="probe",
        conversation_key=conversation_key,
        principal_scope=issued,
    )
    return resolver.conversation(
        channel="probe",
        principal_key=principal_key,
        conversation_key=conversation_key,
        session_id=session_id,
    )


async def _execute(app, core, name, arguments):
    turn = _turn(core)
    return await app.runner.execute_call(
        ToolCall(name=name, arguments=arguments, id=f"call_{name}"),
        core=core,
        turn=turn,
        capability=CapabilityFacade(core),
        principal_scope=_principal_scope(app, core, turn),
        emit_event=app.runner.event_log.emit,
    )


def _install_authored_policy_tool(
    app,
    *,
    name: str,
    module_source: str,
    capability: str = "probe.effect",
    capabilities: list[str] | None = None,
    risk: str = "medium",
    approval_policy: str = "prompt",
) -> None:
    tool_root = app.version_store.active_core_path("assistant") / "agent" / "tools" / name
    tool_root.mkdir(parents=True)
    capability_block = "capabilities: []\n"
    if capabilities:
        capability_block = "capabilities:\n" + "".join(
            f"  - {item}\n"
            for item in capabilities
        )
    (tool_root / "tool.yaml").write_text(
        "entrypoint: module:run\n"
        f"description: Authored policy test tool {name}.\n"
        "input_schema:\n"
        "  type: object\n"
        "  properties: {}\n"
        f"{capability_block}"
        f"capability: {capability}\n"
        f"risk: {risk}\n"
        f"approval_policy: {approval_policy}\n",
        encoding="utf-8",
    )
    (tool_root / "module.py").write_text(module_source, encoding="utf-8")


@pytest.mark.asyncio
async def test_tool_01_authored_prompt_policy_denial_prevents_execution(tmp_path):
    """TOOL-01: authored prompt policy must be enforced before module execution."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = create_app(home=tmp_path / "home", provider_name="fake", workspace=workspace)
    marker = workspace / "authored-tool-ran.txt"
    tool_root = app.version_store.active_core_path("assistant") / "agent" / "tools" / "policy_probe"
    tool_root.mkdir(parents=True)
    (tool_root / "tool.yaml").write_text(
        "entrypoint: module:run\n"
        "description: Authored approval policy probe.\n"
        "input_schema:\n"
        "  type: object\n"
        "  properties: {}\n"
        "capabilities: []\n"
        "capability: probe.effect\n"
        "risk: medium\n"
        "approval_policy: prompt\n",
        encoding="utf-8",
    )
    (tool_root / "module.py").write_text(
        "from pathlib import Path\n\n"
        "def run(ctx, arguments):\n"
        f"    Path({str(marker)!r}).write_text('ran', encoding='utf-8')\n"
        "    return {'content': 'probe-ran'}\n",
        encoding="utf-8",
    )
    approval_provider = RecordingApprovalProvider(["deny"])
    app.approval_runtime.provider = approval_provider
    core = _load_core_with(app, capabilities={"probe.effect": {}})
    entry = next(item for item in app.tool_runtime.registry_for(core) if item.name == "policy_probe")

    result = await _execute(app, core, "policy_probe", {})
    approval_events = [
        event["type"]
        for event in app.runner.event_log.tail(20)
        if event["type"].startswith("approval.")
    ]

    assert entry.source == "authored"
    assert entry.capability == "probe.effect"
    assert entry.approval_policy == "prompt"
    assert {
        "marker_exists": marker.exists(),
        "is_error": result.is_error,
        "approval_requests": len(approval_provider.requests),
        "approval_risks": [request.risk for request in approval_provider.requests],
        "approval_events": approval_events,
        "execution_started": result.data["executionStarted"],
        "decision": result.data["approval"]["value"],
    } == {
        "marker_exists": False,
        "is_error": True,
        "approval_requests": 1,
        "approval_risks": ["medium"],
        "approval_events": ["approval.requested", "approval.decided", "approval.denied"],
        "execution_started": False,
        "decision": "deny",
    }


@pytest.mark.asyncio
async def test_tool_01_authored_approval_uses_bounded_redacted_preview(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    _install_authored_policy_tool(
        app,
        name="preview_probe",
        module_source="def run(ctx, arguments):\n    return {'content': 'probe-ran'}\n",
    )
    provider = RecordingApprovalProvider(["deny"])
    app.approval_runtime.provider = provider
    core = _load_core_with(app, capabilities={"probe.effect": {}})
    secret = "SYNTHETIC_AUTHORED_SECRET_SENTINEL"

    result = await _execute(
        app,
        core,
        "preview_probe",
        {
            "api_key": secret,
            "note": "x" * 5000,
            "nested": {"token": secret, "visible": "ok"},
        },
    )

    preview = provider.requests[0].arguments_preview
    serialized = json.dumps(preview, ensure_ascii=False)
    assert result.is_error is True
    assert preview["api_key"] == "<redacted>"
    assert preview["nested"]["token"] == "<redacted>"
    assert preview["nested"]["visible"] == "ok"
    assert secret not in serialized
    assert len(serialized) <= 2048


@pytest.mark.asyncio
async def test_tool_01_authored_missing_capability_prevents_module_import(tmp_path):
    """TOOL-01: capability denial must happen before authored module import."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = create_app(home=tmp_path / "home", provider_name="fake", workspace=workspace)
    marker = workspace / "authored-tool-imported.txt"
    tool_root = app.version_store.active_core_path("assistant") / "agent" / "tools" / "capability_probe"
    tool_root.mkdir(parents=True)
    (tool_root / "tool.yaml").write_text(
        "entrypoint: module:run\n"
        "description: Authored capability probe.\n"
        "input_schema:\n"
        "  type: object\n"
        "  properties: {}\n"
        "capabilities: []\n"
        "capability: probe.missing\n"
        "risk: medium\n"
        "approval_policy: auto\n",
        encoding="utf-8",
    )
    (tool_root / "module.py").write_text(
        "from pathlib import Path\n\n"
        f"Path({str(marker)!r}).write_text('imported', encoding='utf-8')\n\n"
        "def run(ctx, arguments):\n"
        "    return {'content': 'probe-ran'}\n",
        encoding="utf-8",
    )
    approval_provider = RecordingApprovalProvider(["allow"])
    app.approval_runtime.provider = approval_provider
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))

    result = await _execute(app, core, "capability_probe", {})

    assert result.is_error is True
    assert marker.exists() is False
    assert result.data["executionStarted"] is False
    assert result.content == "capability denied: probe.missing for agent/tools/capability_probe"
    assert approval_provider.requests == []


@pytest.mark.asyncio
async def test_tool_01_authored_plural_capabilities_cannot_self_grant_singular_gate(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = create_app(home=tmp_path / "home", provider_name="fake", workspace=workspace)
    marker = workspace / "authored-self-grant.txt"
    _install_authored_policy_tool(
        app,
        name="self_grant_probe",
        capabilities=["probe.effect"],
        approval_policy="auto",
        module_source=(
            "from pathlib import Path\n\n"
            f"Path({str(marker)!r}).write_text('imported', encoding='utf-8')\n\n"
            "def run(ctx, arguments):\n"
            "    return {'content': 'probe-ran'}\n"
        ),
    )
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))

    result = await _execute(app, core, "self_grant_probe", {})

    assert result.is_error is True
    assert result.data["executionStarted"] is False
    assert marker.exists() is False


@pytest.mark.asyncio
async def test_tool_01_authored_global_auto_cannot_weaken_prompt_policy(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = create_app(home=tmp_path / "home", provider_name="fake", workspace=workspace)
    marker = workspace / "authored-global-auto.txt"
    _install_authored_policy_tool(
        app,
        name="global_auto_probe",
        module_source=(
            "from pathlib import Path\n\n"
            "def run(ctx, arguments):\n"
            f"    Path({str(marker)!r}).write_text('ran', encoding='utf-8')\n"
            "    return {'content': 'probe-ran'}\n"
        ),
    )
    provider = RecordingApprovalProvider(["deny"])
    app.approval_runtime.provider = provider
    app.tool_runtime.global_approval.tools["global_auto_probe"] = "auto"
    core = _load_core_with(app, capabilities={"probe.effect": {}})

    result = await _execute(app, core, "global_auto_probe", {})

    assert result.is_error is True
    assert marker.exists() is False
    assert len(provider.requests) == 1
    assert provider.requests[0].policy == "prompt"


@pytest.mark.asyncio
async def test_tool_01_authored_core_prompt_makes_auto_policy_stricter(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = create_app(home=tmp_path / "home", provider_name="fake", workspace=workspace)
    marker = workspace / "authored-core-prompt.txt"
    _install_authored_policy_tool(
        app,
        name="core_prompt_probe",
        approval_policy="auto",
        module_source=(
            "from pathlib import Path\n\n"
            "def run(ctx, arguments):\n"
            f"    Path({str(marker)!r}).write_text('ran', encoding='utf-8')\n"
            "    return {'content': 'probe-ran'}\n"
        ),
    )
    manifest_path = app.version_store.active_core_path("assistant") / "agent.yaml"
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    raw["approval"] = {"tools": {"core_prompt_probe": "prompt"}}
    manifest_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    provider = RecordingApprovalProvider(["deny"])
    app.approval_runtime.provider = provider
    core = _load_core_with(app, capabilities={"probe.effect": {}})

    result = await _execute(app, core, "core_prompt_probe", {})

    assert result.is_error is True
    assert marker.exists() is False
    assert len(provider.requests) == 1
    assert provider.requests[0].policy == "prompt"


@pytest.mark.asyncio
async def test_tool_01_authored_prompt_without_interactive_route_denies_before_import(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = create_app(home=tmp_path / "home", provider_name="fake", workspace=workspace)
    marker = workspace / "authored-no-route.txt"
    _install_authored_policy_tool(
        app,
        name="no_route_probe",
        module_source=(
            "from pathlib import Path\n\n"
            f"Path({str(marker)!r}).write_text('imported', encoding='utf-8')\n\n"
            "def run(ctx, arguments):\n"
            "    return {'content': 'probe-ran'}\n"
        ),
    )
    core = _load_core_with(app, capabilities={"probe.effect": {}})

    result = await _execute(app, core, "no_route_probe", {})

    assert result.is_error is True
    assert result.data["executionStarted"] is False
    assert result.data["approval"]["reason"] == "no_interactive_route"
    assert marker.exists() is False


@pytest.mark.asyncio
async def test_tool_01_authored_deny_policy_does_not_call_provider(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = create_app(home=tmp_path / "home", provider_name="fake", workspace=workspace)
    marker = workspace / "authored-deny.txt"
    _install_authored_policy_tool(
        app,
        name="deny_probe",
        approval_policy="deny",
        module_source=(
            "from pathlib import Path\n\n"
            "def run(ctx, arguments):\n"
            f"    Path({str(marker)!r}).write_text('ran', encoding='utf-8')\n"
            "    return {'content': 'probe-ran'}\n"
        ),
    )
    provider = RecordingApprovalProvider(["allow"])
    app.approval_runtime.provider = provider
    core = _load_core_with(app, capabilities={"probe.effect": {}})

    result = await _execute(app, core, "deny_probe", {})

    assert result.is_error is True
    assert result.data["executionStarted"] is False
    assert result.data["approval"]["value"] == "deny"
    assert marker.exists() is False
    assert provider.requests == []


@pytest.mark.asyncio
async def test_tool_01_authored_auto_policy_executes_without_provider(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = create_app(home=tmp_path / "home", provider_name="fake", workspace=workspace)
    marker = workspace / "authored-auto.txt"
    _install_authored_policy_tool(
        app,
        name="auto_probe",
        approval_policy="auto",
        module_source=(
            "from pathlib import Path\n\n"
            "def run(ctx, arguments):\n"
            f"    Path({str(marker)!r}).write_text('ran', encoding='utf-8')\n"
            "    return {'content': 'probe-ran'}\n"
        ),
    )
    provider = RecordingApprovalProvider(["deny"])
    app.approval_runtime.provider = provider
    core = _load_core_with(app, capabilities={"probe.effect": {}})

    result = await _execute(app, core, "auto_probe", {})
    approval = next(
        event
        for event in reversed(app.runner.event_log.tail(20))
        if event["type"] == "approval.decided"
    )

    assert result.is_error is False
    assert result.content == "probe-ran"
    assert marker.exists() is True
    assert provider.requests == []
    assert approval["automatic"] is True
    assert approval["policy"] == "auto"


@pytest.mark.asyncio
async def test_tool_01_authored_session_allow_reuses_same_rule(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    _install_authored_policy_tool(
        app,
        name="session_allow_probe",
        module_source="def run(ctx, arguments):\n    return {'content': 'probe-ran'}\n",
    )
    provider = RecordingApprovalProvider(["always_allow_for_session", "deny"])
    app.approval_runtime.provider = provider
    core = _load_core_with(app, capabilities={"probe.effect": {}})

    first = await _execute(app, core, "session_allow_probe", {"value": 1})
    second = await _execute(app, core, "session_allow_probe", {"value": 2})

    assert first.is_error is False
    assert second.is_error is False
    assert len(provider.requests) == 1


@pytest.mark.asyncio
async def test_tool_01_authored_exception_reports_execution_started(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = create_app(home=tmp_path / "home", provider_name="fake", workspace=workspace)
    marker = workspace / "authored-exception.txt"
    _install_authored_policy_tool(
        app,
        name="exception_probe",
        approval_policy="auto",
        module_source=(
            "from pathlib import Path\n\n"
            "def run(ctx, arguments):\n"
            f"    Path({str(marker)!r}).write_text('started', encoding='utf-8')\n"
            "    raise RuntimeError('synthetic authored failure')\n"
        ),
    )
    core = _load_core_with(app, capabilities={"probe.effect": {}})

    result = await _execute(app, core, "exception_probe", {})

    assert marker.exists() is True
    assert result.is_error is True
    assert result.content == "synthetic authored failure"
    assert result.data["executionStarted"] is True


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
async def test_read_file_outside_workspace_requires_approval_and_can_be_allowed(tmp_path):
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    report = outside / "report.md"
    report.write_text("external report", encoding="utf-8")
    app = create_app(home=tmp_path / "home", provider_name="fake", workspace=workspace)
    approval_provider = RecordingApprovalProvider(["allow"])
    app.approval_runtime.provider = approval_provider
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))

    result = await _execute(app, core, "read_file", {"path": str(report)})

    assert result.is_error is False
    assert result.content == "external report"
    assert result.data["path"] == str(report.resolve())
    assert len(approval_provider.requests) == 1
    assert approval_provider.requests[0].risk == "high"
    assert approval_provider.requests[0].target == str(report.resolve())
    events = [event["type"] for event in app.runner.event_log.tail(20)]
    assert "approval.requested" in events


@pytest.mark.asyncio
async def test_read_file_outside_workspace_denial_does_not_read_file(tmp_path):
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    report = outside / "report.md"
    report.write_text("external report", encoding="utf-8")
    app = create_app(home=tmp_path / "home", provider_name="fake", workspace=workspace)
    app.approval_runtime.provider = StaticApprovalProvider("deny")
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))

    result = await _execute(app, core, "read_file", {"path": str(report)})

    assert result.is_error is True
    assert result.data["executionStarted"] is False
    assert "external report" not in result.content


@pytest.mark.asyncio
async def test_read_file_expands_home_for_outside_workspace_approval(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    home = tmp_path / "home-dir"
    workspace.mkdir()
    home.mkdir()
    note = home / "note.txt"
    note.write_text("home note", encoding="utf-8")
    _set_test_home(monkeypatch, home)
    app = create_app(home=tmp_path / "runtime", provider_name="fake", workspace=workspace)
    app.approval_runtime.provider = StaticApprovalProvider("allow")
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))

    result = await _execute(app, core, "read_file", {"path": "~/note.txt"})

    assert result.is_error is False
    assert result.content == "home note"
    assert result.data["path"] == str(note.resolve())


@pytest.mark.asyncio
async def test_search_files_outside_workspace_requires_approval_and_skips_sensitive_by_default(tmp_path):
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    (outside / "alpha.txt").write_text("needle\n", encoding="utf-8")
    (outside / ".env").write_text("needle secret\n", encoding="utf-8")
    (outside / "id_ed25519").write_text("needle private key\n", encoding="utf-8")
    app = create_app(home=tmp_path / "home", provider_name="fake", workspace=workspace)
    approval_provider = RecordingApprovalProvider(["allow"])
    app.approval_runtime.provider = approval_provider
    core = _load_core_with(app, capabilities={"fs.read": {"scope": "workspace"}})

    result = await _execute(
        app,
        core,
        "search_files",
        {"query": "needle", "path": str(outside), "pattern": "*.*"},
    )

    assert result.is_error is False
    assert "alpha.txt:1" in result.content
    assert ".env" not in result.content
    assert "id_ed25519" not in result.content
    assert len(approval_provider.requests) == 1
    assert approval_provider.requests[0].risk == "high"
    assert approval_provider.requests[0].target == str(outside.resolve())


@pytest.mark.asyncio
async def test_search_files_outside_workspace_denial_does_not_search(tmp_path):
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    (outside / "alpha.txt").write_text("needle\n", encoding="utf-8")
    app = create_app(home=tmp_path / "home", provider_name="fake", workspace=workspace)
    app.approval_runtime.provider = StaticApprovalProvider("deny")
    core = _load_core_with(app, capabilities={"fs.read": {"scope": "workspace"}})

    result = await _execute(app, core, "search_files", {"query": "needle", "path": str(outside)})

    assert result.is_error is True
    assert result.data["executionStarted"] is False
    assert "alpha.txt" not in result.content


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
async def test_mutating_tools_and_terminal_cwd_reject_workspace_escape_before_execution(tmp_path):
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside.txt"
    workspace.mkdir()
    outside.write_text("outside", encoding="utf-8")
    app = create_app(home=tmp_path / "home", provider_name="fake", workspace=workspace)
    app.approval_runtime.provider = StaticApprovalProvider("allow")
    core = _load_core_with(
        app,
        capabilities={
            "fs.write": {"scope": "workspace"},
            "terminal.exec": {"scope": "workspace"},
        },
    )

    write = await _execute(app, core, "write_file", {"path": str(outside), "content": "changed"})
    patch = await _execute(app, core, "patch", {"path": str(outside), "old": "outside", "new": "changed"})
    terminal = await _execute(app, core, "terminal", {"command": "pwd", "cwd": str(tmp_path)})

    assert write.is_error is True
    assert write.data["executionStarted"] is False
    assert patch.is_error is True
    assert patch.data["executionStarted"] is False
    assert terminal.is_error is True
    assert terminal.data["executionStarted"] is False
    assert outside.read_text(encoding="utf-8") == "outside"


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
    missing_command = await _execute(app, core, "terminal", {"cwd": "."})
    bad_env = await _execute(app, core, "terminal", {"command": "printf hello", "env": ["BAD"]})

    app.approval_runtime.provider = StaticApprovalProvider("deny")
    denied = await _execute(app, core, "terminal", {"command": "rm denied.txt"})

    assert success.is_error is False
    assert "hello" in success.content
    assert success.display_output is not None
    assert "$ printf hello" in success.display_output
    assert "cwd: ." in success.display_output
    assert success.content.startswith("exit_code:")
    assert timeout.is_error is True
    assert timeout.data["timed_out"] is True
    assert timeout.display_output is not None
    assert "$ sleep 2" in timeout.display_output
    assert escape.is_error is True
    assert escape.data["executionStarted"] is False
    assert missing_command.is_error is True
    assert missing_command.content == "command is required"
    assert missing_command.display_output is not None
    assert "cwd: ." in missing_command.display_output
    assert bad_env.is_error is True
    assert bad_env.content == "env must be an object"
    assert bad_env.display_output is not None
    assert "$ printf hello" in bad_env.display_output
    assert denied.is_error is True
    assert denied.data["executionStarted"] is False
    assert denied.display_output is not None
    assert "$ rm denied.txt" in denied.display_output
    assert (workspace / "denied.txt").exists()


@pytest.mark.cross_platform
def test_windows_terminal_printf_compat_formats_common_smoke_command():
    assert tool_runtime._format_windows_printf("%s\\n", ["hello"]) == "hello\n"

    translated = tool_runtime._windows_posix_compat_command("printf '%s\\n' hello")

    assert translated is not None
    assert "_format_windows_printf" in translated
    assert "printf" not in translated.split(" -c ", 1)[0]


@pytest.mark.asyncio
@pytest.mark.cross_platform
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
async def test_env_01_terminal_subprocess_does_not_inherit_host_secret(monkeypatch, tmp_path):
    """ENV-01: terminal subprocesses do not inherit synthetic host secrets."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    variable = "DEMIURGE_ENV_01_SYNTHETIC_PROVIDER_SECRET"
    sentinel = "SYNTHETIC_ENV_01_SECRET_SENTINEL"
    monkeypatch.setenv(variable, sentinel)
    app = create_app(home=tmp_path / "home", provider_name="fake", workspace=workspace)
    app.approval_runtime.provider = StaticApprovalProvider("allow")
    core = _load_core_with(app, capabilities={"terminal.exec": {"scope": "workspace"}})

    result = await _execute(app, core, "terminal", {"command": "env"})

    assert result.is_error is False
    assert result.data["executionStarted"] is True
    assert sentinel not in result.content


@pytest.mark.asyncio
async def test_env_01_project_code_execution_requires_approval(tmp_path):
    """ENV-01: commands that execute workspace code are not automatically approved."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "test_env_01_project_code.py").write_text(
        "def test_harmless_project_code():\n"
        "    assert True\n",
        encoding="utf-8",
    )
    app = create_app(home=tmp_path / "home", provider_name="fake", workspace=workspace)
    app.approval_runtime.provider = StaticApprovalProvider("deny")
    core = _load_core_with(app, capabilities={"terminal.exec": {"scope": "workspace"}})

    result = await _execute(
        app,
        core,
        "terminal",
        {"command": "python -m pytest -q test_env_01_project_code.py"},
    )
    approval_events = [
        event
        for event in app.runner.event_log.tail(20)
        if event["type"] == "approval.decided"
    ]

    assert {
        "is_error": result.is_error,
        "execution_started": result.data["executionStarted"],
        "automatic": approval_events[-1]["automatic"],
    } == {
        "is_error": True,
        "execution_started": False,
        "automatic": False,
    }


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


@pytest.mark.parametrize(
    "command_template",
    [
        pytest.param('echo "$(printf {sentinel})"', id="double-quoted"),
        pytest.param("echo ＇$(printf {sentinel})＇", id="fullwidth-apostrophe"),
        pytest.param("echo ＼$(printf {sentinel})", id="fullwidth-backslash"),
        pytest.param("echo $\\\n(printf {sentinel})", id="line-continuation"),
        pytest.param("printf {sentinel}_%s $[1+1]", id="legacy-arithmetic"),
    ],
)
@pytest.mark.asyncio
async def test_sec_01_terminal_does_not_auto_execute_shell_expansion(tmp_path, command_template):
    """SEC-01: runtime must not auto-execute a harmless expansion sentinel."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = create_app(home=tmp_path / "home", provider_name="fake", workspace=workspace)
    app.approval_runtime.provider = StaticApprovalProvider("deny")
    core = _load_core_with(app, capabilities={"terminal.exec": {"scope": "workspace"}})
    sentinel = "DEMIURGE_SEC_01_SUBSTITUTION_SENTINEL"

    result = await _execute(
        app,
        core,
        "terminal",
        {"command": command_template.format(sentinel=sentinel)},
    )

    assert {
        "is_error": result.is_error,
        "execution_started": result.data["executionStarted"],
        "sentinel_exposed": sentinel in result.content,
    } == {
        "is_error": True,
        "execution_started": False,
        "sentinel_exposed": False,
    }


@pytest.mark.asyncio
async def test_sec_01_global_auto_does_not_override_shell_expansion_guard(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    home = tmp_path / "home"
    app = create_app(home=home, provider_name="fake", workspace=workspace)
    fallback = home / "agents" / "agent.yaml"
    raw_fallback = yaml.safe_load(fallback.read_text(encoding="utf-8"))
    raw_fallback["approval"] = {"tools": {"terminal": "auto"}}
    fallback.write_text(yaml.safe_dump(raw_fallback, sort_keys=False), encoding="utf-8")
    app = create_app(home=home, provider_name="fake", workspace=workspace)
    app.approval_runtime.provider = StaticApprovalProvider("deny")
    core = _load_core_with(app, capabilities={"terminal.exec": {"scope": "workspace"}})
    sentinel = "DEMIURGE_SEC_01_GLOBAL_AUTO_SENTINEL"

    result = await _execute(
        app,
        core,
        "terminal",
        {"command": f'echo "$(printf {sentinel})"'},
    )

    assert result.is_error is True
    assert result.data["executionStarted"] is False
    assert sentinel not in result.content
    approval = next(event for event in reversed(app.runner.event_log.tail(20)) if event["type"] == "approval.decided")
    assert approval["automatic"] is False
    assert approval["policy"] == "prompt"


@pytest.mark.asyncio
async def test_sec_01_session_allow_is_scoped_to_the_exact_expansion_command(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = create_app(home=tmp_path / "home", provider_name="fake", workspace=workspace)
    provider = RecordingApprovalProvider(["always_allow_for_session", "deny"])
    app.approval_runtime.provider = provider
    core = _load_core_with(app, capabilities={"terminal.exec": {"scope": "workspace"}})

    first = await _execute(app, core, "terminal", {"command": 'echo "$(printf FIRST_SENTINEL)"'})
    second = await _execute(app, core, "terminal", {"command": 'echo "$(printf SECOND_SENTINEL)"'})

    assert first.is_error is False
    assert first.data["executionStarted"] is True
    assert "FIRST_SENTINEL" in first.content
    assert second.is_error is True
    assert second.data["executionStarted"] is False
    assert "SECOND_SENTINEL" not in second.content
    assert len(provider.requests) == 2
    assert provider.requests[0].cache_key != provider.requests[1].cache_key
    assert all("SENTINEL" not in request.cache_key for request in provider.requests)
    assert all(request.cache_key.startswith("terminal:terminal.exec:command-substitution:") for request in provider.requests)


@pytest.mark.asyncio
async def test_sec_01_session_allow_is_scoped_to_the_exact_shell_eval(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = create_app(home=tmp_path / "home", provider_name="fake", workspace=workspace)
    provider = RecordingApprovalProvider(["always_allow_for_session", "deny"])
    app.approval_runtime.provider = provider
    core = _load_core_with(app, capabilities={"terminal.exec": {"scope": "workspace"}})

    first = await _execute(app, core, "terminal", {"command": "sh -c 'printf FIRST_SENTINEL'"})
    second = await _execute(app, core, "terminal", {"command": "sh -c 'printf SECOND_SENTINEL'"})

    assert first.is_error is False
    assert first.data["executionStarted"] is True
    assert "FIRST_SENTINEL" in first.content
    assert second.is_error is True
    assert second.data["executionStarted"] is False
    assert "SECOND_SENTINEL" not in second.content
    assert len(provider.requests) == 2
    assert provider.requests[0].cache_key != provider.requests[1].cache_key
    assert all(request.cache_key.startswith("terminal:terminal.exec:shell-eval:") for request in provider.requests)


@pytest.mark.asyncio
async def test_sec_01_expansion_approval_fingerprint_includes_env_overlay(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = create_app(home=tmp_path / "home", provider_name="fake", workspace=workspace)
    provider = RecordingApprovalProvider(["always_allow_for_session", "deny"])
    app.approval_runtime.provider = provider
    core = _load_core_with(app, capabilities={"terminal.exec": {"scope": "workspace"}})
    command = 'printf "%s" "$VALUE"'

    first = await _execute(app, core, "terminal", {"command": command, "env": {"VALUE": "FIRST_SENTINEL"}})
    second = await _execute(app, core, "terminal", {"command": command, "env": {"VALUE": "SECOND_SENTINEL"}})

    assert first.is_error is False
    assert first.data["executionStarted"] is True
    assert "FIRST_SENTINEL" in first.content
    assert second.is_error is True
    assert second.data["executionStarted"] is False
    assert "SECOND_SENTINEL" not in second.content
    assert len(provider.requests) == 2
    assert provider.requests[0].cache_key != provider.requests[1].cache_key
    assert all("SENTINEL" not in request.cache_key for request in provider.requests)


@pytest.mark.asyncio
async def test_sec_01_expansion_approval_fingerprint_includes_execution_options(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = create_app(home=tmp_path / "home", provider_name="fake", workspace=workspace)
    provider = RecordingApprovalProvider(["always_allow_for_session", "deny"])
    app.approval_runtime.provider = provider
    core = _load_core_with(app, capabilities={"terminal.exec": {"scope": "workspace"}})
    command = 'printf "%s" "$VALUE"'
    env = {"VALUE": "OPTIONS_SENTINEL"}

    first = await _execute(app, core, "terminal", {"command": command, "env": env, "timeout_seconds": 1})
    second = await _execute(app, core, "terminal", {"command": command, "env": env, "timeout_seconds": 2})

    assert first.is_error is False
    assert first.data["executionStarted"] is True
    assert "OPTIONS_SENTINEL" in first.content
    assert second.is_error is True
    assert second.data["executionStarted"] is False
    assert "OPTIONS_SENTINEL" not in second.content
    assert len(provider.requests) == 2
    assert provider.requests[0].cache_key != provider.requests[1].cache_key


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
async def test_resolved_terminal_deny_blocks_safe_commands(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = create_app(
        home=tmp_path / "home",
        provider_name="fake",
        workspace=workspace,
    )
    app.approval_runtime.provider = StaticApprovalProvider("allow")
    core = _load_core_with(
        app,
        capabilities={"terminal.exec": {"scope": "workspace"}},
        tool_metadata={
            "terminal": {
                "approval_policy": "deny",
            }
        },
    )

    result = await _execute(app, core, "terminal", {"command": "printf no"})

    assert result.is_error is True
    assert result.data["executionStarted"] is False
    assert result.data["approval"]["value"] == "deny"
    assert "no" not in result.content


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
    assert started.display_output is not None
    assert "$ printf ready" in started.display_output
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
        metadata={"private_token": "task-list-private-metadata"},
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
    app.task_worker.set_result_ref(current.task_id, "file:///operator/private/task-list.json")

    listed = await _execute(app, core, "task_list", {"kind": "terminal.exec"})

    assert current.task_id in listed.content
    assert other.task_id not in listed.content
    assert "task-list-private-metadata" not in listed.content
    assert "operator/private/task-list.json" not in listed.content
    assert set(listed.data["tasks"][0]) == {
        "task_id",
        "kind",
        "status",
        "running",
        "started_at",
        "completed_at",
        "summary",
    }


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
async def test_tool_03_evolve_promote_denial_prevents_adapter_call(tmp_path):
    """TOOL-03: registry prompt denial must precede core promotion."""

    app = create_app(home=tmp_path / "home", provider_name="fake")
    fake = FakeEvolutionAdapter()
    app.tool_runtime.evolution_runtime = fake
    app.approval_runtime.provider = StaticApprovalProvider("deny")
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    entry = next(item for item in app.tool_runtime.registry_for(core) if item.name == "evolve_core")

    result = await _execute(app, core, "evolve_core", {"action": "promote", "run_id": "run_probe"})
    approval_events = [
        event["type"]
        for event in app.runner.event_log.tail(20)
        if event["type"].startswith("approval.")
    ]

    assert entry.source == "builtin"
    assert entry.capability == "tool.call:evolve_core"
    assert entry.risk == "high"
    assert entry.approval_policy == "prompt"
    assert fake.promote_calls == 0
    assert result.is_error is True
    assert result.data["executionStarted"] is False
    assert result.data["approval"]["value"] == "deny"
    assert approval_events == ["approval.requested", "approval.decided", "approval.denied"]


@pytest.mark.asyncio
async def test_tool_03_rollback_denial_prevents_version_store_call(tmp_path):
    """TOOL-03: registry prompt denial must precede core rollback."""

    app = create_app(home=tmp_path / "home", provider_name="fake")
    fake = FakeVersionStore()
    app.tool_runtime.version_store = fake
    app.approval_runtime.provider = StaticApprovalProvider("deny")
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))

    result = await _execute(app, core, "rollback_core", {"target": "previous"})

    assert fake.rollback_calls == 0
    assert result.is_error is True
    assert result.data["executionStarted"] is False
    assert result.data["approval"]["value"] == "deny"


@pytest.mark.asyncio
async def test_tool_03_evolve_background_denial_prevents_task_creation(tmp_path):
    """TOOL-03: background evolution must be approved before task creation."""

    app = create_app(home=tmp_path / "home", provider_name="fake")
    fake = FakeTaskWorker()
    app.tool_runtime.task_worker = fake
    app.approval_runtime.provider = StaticApprovalProvider("deny")
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))

    result = await _execute(
        app,
        core,
        "evolve_core",
        {"action": "start", "goal": "safe fake goal", "background": True},
    )

    assert fake.start_calls == 0
    assert result.is_error is True
    assert result.data["executionStarted"] is False
    assert result.data["approval"]["value"] == "deny"


@pytest.mark.asyncio
async def test_tool_03_evolve_foreground_denial_prevents_adapter_call(tmp_path):
    """TOOL-03: foreground evolution must be approved before adapter execution."""

    app = create_app(home=tmp_path / "home", provider_name="fake")
    fake = FakeEvolutionAdapter()
    app.tool_runtime.evolution_runtime = fake
    app.approval_runtime.provider = StaticApprovalProvider("deny")
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))

    result = await _execute(
        app,
        core,
        "evolve_core",
        {"action": "start", "goal": "safe fake goal", "background": False},
    )

    assert fake.start_calls == 0
    assert result.is_error is True
    assert result.data["executionStarted"] is False
    assert result.data["approval"]["value"] == "deny"


@pytest.mark.asyncio
async def test_tool_03_evolve_review_denial_prevents_adapter_call(tmp_path):
    """TOOL-03: evolve review must be approved before adapter execution."""

    app = create_app(home=tmp_path / "home", provider_name="fake")
    fake = FakeEvolutionAdapter()
    app.tool_runtime.evolution_runtime = fake
    app.approval_runtime.provider = StaticApprovalProvider("deny")
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))

    result = await _execute(app, core, "evolve_core", {"action": "review", "run_id": "run_probe"})

    assert fake.review_calls == 0
    assert result.is_error is True
    assert result.data["executionStarted"] is False
    assert result.data["approval"]["value"] == "deny"


@pytest.mark.asyncio
async def test_tool_03_evolve_discard_denial_prevents_adapter_call(tmp_path):
    """TOOL-03: evolve discard must be approved before adapter execution."""

    app = create_app(home=tmp_path / "home", provider_name="fake")
    fake = FakeEvolutionAdapter()
    app.tool_runtime.evolution_runtime = fake
    app.approval_runtime.provider = StaticApprovalProvider("deny")
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))

    result = await _execute(app, core, "evolve_core", {"action": "discard", "run_id": "run_probe"})

    assert fake.discard_calls == 0
    assert result.is_error is True
    assert result.data["executionStarted"] is False
    assert result.data["approval"]["value"] == "deny"


@pytest.mark.asyncio
async def test_tool_03_evolve_allow_uses_resolved_entry_and_safe_preview(tmp_path):
    """TOOL-03: approval requests must preserve resolved registry policy safely."""

    app = create_app(home=tmp_path / "home", provider_name="fake")
    fake = FakeEvolutionAdapter()
    provider = RecordingApprovalProvider(["allow"])
    app.tool_runtime.evolution_runtime = fake
    app.approval_runtime.provider = provider
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))

    result = await _execute(
        app,
        core,
        "evolve_core",
        {
            "action": "promote",
            "run_id": "run_probe",
            "reason": "r" * 5000,
            "secret_token": "synthetic-secret",
        },
    )

    assert fake.promote_calls == 1
    assert result.is_error is False
    assert len(provider.requests) == 1
    request = provider.requests[0]
    assert request.tool_name == "evolve_core"
    assert request.capability == "tool.call:evolve_core"
    assert request.risk == "high"
    assert request.policy == "prompt"
    assert request.action == "evolve.promote"
    assert request.target == "assistant:run_probe"
    assert request.arguments_preview["secret_token"] == "<redacted>"
    assert len(request.arguments_preview["reason"]) <= 300
    assert "[truncated 4744 chars]" in request.arguments_preview["reason"]
    assert len(json.dumps(request.arguments_preview, ensure_ascii=False)) <= 2048


@pytest.mark.asyncio
async def test_tool_03_evolve_session_allow_is_cached_per_mutation_action(tmp_path):
    """TOOL-03: a session allow may repeat one rule but not authorize another action."""

    app = create_app(home=tmp_path / "home", provider_name="fake")
    fake = FakeEvolutionAdapter()
    provider = RecordingApprovalProvider(["always_allow_for_session", "deny"])
    app.tool_runtime.evolution_runtime = fake
    app.approval_runtime.provider = provider
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))

    first = await _execute(app, core, "evolve_core", {"action": "review", "run_id": "run_probe"})
    cached = await _execute(app, core, "evolve_core", {"action": "review", "run_id": "run_probe"})
    different_action = await _execute(app, core, "evolve_core", {"action": "discard", "run_id": "run_probe"})

    assert first.is_error is False
    assert cached.is_error is False
    assert different_action.is_error is True
    assert fake.review_calls == 2
    assert fake.discard_calls == 0
    assert [request.action for request in provider.requests] == ["evolve.review", "evolve.discard"]
    assert different_action.data["approval"]["value"] == "deny"


@pytest.mark.asyncio
async def test_tool_03_global_auto_cannot_weaken_builtin_prompt_policy(tmp_path):
    """TOOL-03: global policy may tighten but cannot lower the registry baseline."""

    app = create_app(home=tmp_path / "home", provider_name="fake")
    fake = FakeEvolutionAdapter()
    provider = RecordingApprovalProvider(["deny"])
    app.tool_runtime.evolution_runtime = fake
    app.tool_runtime.global_approval.tools["evolve_core"] = "auto"
    app.approval_runtime.provider = provider
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))

    result = await _execute(app, core, "evolve_core", {"action": "discard", "run_id": "run_probe"})

    assert fake.discard_calls == 0
    assert len(provider.requests) == 1
    assert provider.requests[0].policy == "prompt"
    assert result.data["approval"]["value"] == "deny"


@pytest.mark.asyncio
async def test_tool_03_core_deny_prevents_adapter_without_calling_provider(tmp_path):
    """TOOL-03: a stricter core policy must deny before interactive routing."""

    app = create_app(home=tmp_path / "home", provider_name="fake")
    fake = FakeEvolutionAdapter()
    provider = RecordingApprovalProvider(["allow"])
    app.tool_runtime.evolution_runtime = fake
    app.approval_runtime.provider = provider
    manifest_path = app.version_store.active_core_path("assistant") / "agent.yaml"
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    raw["approval"] = {"tools": {"evolve_core": "deny"}}
    manifest_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))

    result = await _execute(app, core, "evolve_core", {"action": "discard", "run_id": "run_probe"})

    assert fake.discard_calls == 0
    assert provider.requests == []
    assert result.data["approval"]["value"] == "deny"
    assert result.data["approval"]["reason"] == "denied by approval policy"


@pytest.mark.asyncio
async def test_tool_03_no_interactive_route_denies_before_adapter_call(tmp_path):
    """TOOL-03: prompt policy without a route must fail closed."""

    app = create_app(home=tmp_path / "home", provider_name="fake")
    fake = FakeEvolutionAdapter()
    app.tool_runtime.evolution_runtime = fake
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))

    result = await _execute(app, core, "evolve_core", {"action": "discard", "run_id": "run_probe"})

    assert fake.discard_calls == 0
    assert result.data["approval"]["value"] == "deny"
    assert result.data["approval"]["reason"] == "no_interactive_route"


@pytest.mark.asyncio
async def test_tool_03_capability_failure_precedes_approval_and_adapter(tmp_path):
    """TOOL-03: the resolved singular capability remains the first mutation gate."""

    app = create_app(home=tmp_path / "home", provider_name="fake")
    fake = FakeEvolutionAdapter()
    provider = RecordingApprovalProvider(["allow"])
    app.tool_runtime.evolution_runtime = fake
    app.approval_runtime.provider = provider
    manifest_path = app.version_store.active_core_path("assistant") / "agent.yaml"
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    raw["capabilities"]["defaults"].pop("tool.call:evolve_core", None)
    manifest_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))

    result = await _execute(app, core, "evolve_core", {"action": "discard", "run_id": "run_probe"})

    assert fake.discard_calls == 0
    assert provider.requests == []
    assert result.is_error is True
    assert result.data["executionStarted"] is False
    assert "capability denied: tool.call:evolve_core" in result.content


@pytest.mark.asyncio
async def test_tool_03_builtin_dispatch_uses_the_resolved_metadata_override(tmp_path):
    """TOOL-03: visibility, capability, risk, policy, and dispatch share one entry."""

    app = create_app(home=tmp_path / "home", provider_name="fake")
    fake = FakeEvolutionAdapter()
    provider = RecordingApprovalProvider(["allow"])
    app.tool_runtime.evolution_runtime = fake
    app.approval_runtime.provider = provider
    manifest_path = app.version_store.active_core_path("assistant") / "agent.yaml"
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    raw["tools"]["metadata"]["evolve_core"] = {
        "risk": "critical",
        "capability": "tool.call:core_mutation_probe",
        "approval_policy": "deny",
    }
    defaults = raw["capabilities"]["defaults"]
    defaults.pop("tool.call:evolve_core", None)
    defaults["tool.call:core_mutation_probe"] = {"scope": "core"}
    manifest_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    entry = next(item for item in app.tool_runtime.registry_for(core) if item.name == "evolve_core")

    result = await _execute(app, core, "evolve_core", {"action": "discard", "run_id": "run_probe"})
    approval = next(
        event
        for event in app.runner.event_log.tail(20)
        if event["type"] == "approval.decided"
    )

    assert (entry.capability, entry.risk, entry.approval_policy) == (
        "tool.call:core_mutation_probe",
        "critical",
        "deny",
    )
    assert fake.discard_calls == 0
    assert provider.requests == []
    assert result.data["approval"]["value"] == "deny"
    assert {
        "tool_name": approval["tool_name"],
        "capability": approval["capability"],
        "risk": approval["risk"],
        "policy": approval["policy"],
        "action": approval["action"],
    } == {
        "tool_name": "evolve_core",
        "capability": "tool.call:core_mutation_probe",
        "risk": "critical",
        "policy": "deny",
        "action": "evolve.discard",
    }


@pytest.mark.asyncio
async def test_evolve_core_background_creates_candidate_without_promoting(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    app.approval_runtime.provider = StaticApprovalProvider("allow")
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
    app.approval_runtime.provider = StaticApprovalProvider("allow")
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
    app.approval_runtime.provider = StaticApprovalProvider("allow")
    await app.runner.run_turn("alpha search target")
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    before = app.session_runtime.message_count(app.runner.session_id)

    result = await _execute(app, core, "session_search", {"query": "alpha", "limit": 5})
    after = app.session_runtime.message_count(app.runner.session_id)

    assert result.is_error is False
    assert "alpha search target" in result.content
    assert after == before


@pytest.mark.asyncio
async def test_session_search_requires_session_read_capability(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    manifest_path = app.version_store.active_core_path("assistant") / "agent.yaml"
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    raw.setdefault("capabilities", {}).setdefault("defaults", {}).pop(
        "session.read",
        None,
    )
    manifest_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))

    result = await _execute(
        app,
        core,
        "session_search",
        {"query": "secret history", "limit": 5},
    )

    assert result.is_error is True
    assert result.data == {
        "executionStarted": False,
        "denial": "capability",
    }
    assert result.content == "capability denied: session.read for host"
    await app.close()


@pytest.mark.asyncio
async def test_session_search_core_metadata_cannot_replace_host_capability(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    manifest_path = app.version_store.active_core_path("assistant") / "agent.yaml"
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    defaults = raw.setdefault("capabilities", {}).setdefault("defaults", {})
    defaults.pop("session.read", None)
    defaults["task.control"] = {"scope": "session"}
    raw.setdefault("tools", {}).setdefault("metadata", {})["session_search"] = {
        "capability": "task.control",
        "risk": "medium",
        "approval_policy": "prompt",
    }
    manifest_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    app.approval_runtime.provider = StaticApprovalProvider("allow")

    result = await _execute(
        app,
        core,
        "session_search",
        {"query": "secret history", "limit": 5},
    )

    assert result.is_error is True
    assert result.data == {
        "executionStarted": False,
        "denial": "capability",
    }
    assert result.content == "capability denied: session.read for host"
    await app.close()


@pytest.mark.asyncio
async def test_session_search_approval_denial_prevents_history_read(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    manifest_path = app.version_store.active_core_path("assistant") / "agent.yaml"
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    raw.setdefault("capabilities", {}).setdefault("defaults", {})[
        "session.read"
    ] = {"scope": "session"}
    manifest_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    turn = _turn(core)
    principal_scope = _principal_scope(app, core, turn)
    app.session_runtime.append_message(
        turn.session_id,
        role="user",
        content="private history marker",
    )
    app.approval_runtime.provider = StaticApprovalProvider("deny")

    result = await app.runner.execute_call(
        ToolCall(
            name="session_search",
            arguments={"query": "private", "limit": 5},
            id="call_session_search",
        ),
        core=core,
        turn=turn,
        capability=CapabilityFacade(core),
        principal_scope=principal_scope,
        emit_event=app.runner.event_log.emit,
    )

    assert result.is_error is True
    assert result.data["executionStarted"] is False
    assert result.data["approval"]["value"] == "deny"
    assert "private history marker" not in result.content
    await app.close()


@pytest.mark.asyncio
async def test_session_search_does_not_read_another_principal_session(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    app.approval_runtime.provider = StaticApprovalProvider("allow")
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    scope_a = _conversation_scope(app, core, "a")
    scope_b = _conversation_scope(app, core, "b")
    app.session_runtime.append_message(
        scope_a.session_id,
        role="user",
        content="alpha visible marker",
    )
    app.session_runtime.append_message(
        scope_b.session_id,
        role="user",
        content="alpha private marker",
    )
    turn_a = TurnContext(
        session_id=scope_a.session_id,
        turn_id="turn_a",
        core_id=core.core_id,
        core_revision=core.revision,
        user_input=AgentInput(content="search alpha"),
    )

    result = await app.runner.execute_call(
        ToolCall(
            name="session_search",
            arguments={"query": "alpha", "limit": 10},
            id="call_session_search",
        ),
        core=core,
        turn=turn_a,
        capability=CapabilityFacade(core),
        principal_scope=scope_a,
        emit_event=app.runner.event_log.emit,
    )
    browse = await app.runner.execute_call(
        ToolCall(
            name="session_search",
            arguments={"limit": 10},
            id="call_session_browse",
        ),
        core=core,
        turn=turn_a,
        capability=CapabilityFacade(core),
        principal_scope=scope_a,
        emit_event=app.runner.event_log.emit,
    )
    direct = await app.runner.execute_call(
        ToolCall(
            name="session_search",
            arguments={"session_id": scope_b.session_id, "limit": 10},
            id="call_session_direct",
        ),
        core=core,
        turn=turn_a,
        capability=CapabilityFacade(core),
        principal_scope=scope_a,
        emit_event=app.runner.event_log.emit,
    )

    assert result.is_error is False
    assert "alpha visible marker" in result.content
    assert "alpha private marker" not in result.content
    assert scope_b.session_id not in result.content
    assert browse.is_error is False
    assert scope_a.session_id in browse.content
    assert scope_b.session_id not in browse.content
    assert direct.is_error is True
    assert "alpha private marker" not in direct.content
    await app.close()


@pytest.mark.asyncio
async def test_session_search_operator_scope_does_not_browse_legacy_history(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    app.approval_runtime.provider = StaticApprovalProvider("allow")
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    app.session_runtime.create_session(
        session_id="session_legacy_search",
        core_id="assistant",
        core_revision="legacy-rev",
    )
    app.session_runtime.append_message(
        "session_legacy_search",
        role="user",
        content="legacy-search-private-marker",
    )

    result = await _execute(
        app,
        core,
        "session_search",
        {"query": "legacy-search-private-marker", "limit": 10},
    )
    browse = await _execute(app, core, "session_search", {"limit": 50})

    assert result.is_error is False
    assert "legacy-search-private-marker" not in result.content
    assert "session_legacy_search" not in browse.content
    await app.close()


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
        "run_" + "terminal",
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
    removed_terminal_alias = "run_" + "terminal"
    payload = json.loads(result.content)
    assert payload["success"] is True
    names = {tool["name"] for tool in payload["tools"]}
    assert {"tools_list", "task_list", "skills_list", "read_file", "write_file", "patch", "terminal", "echo"}.issubset(names)
    assert {"jo" + "b", "process", removed_terminal_alias}.isdisjoint(names)
    read_file = next(tool for tool in payload["tools"] if tool["name"] == "read_file")
    assert read_file["source"] == "builtin"
    assert read_file["capability"] == "fs.read"
    terminal = next(tool for tool in payload["tools"] if tool["name"] == "terminal")
    assert terminal["capability"] == "terminal.exec"
    assert terminal["approval_policy"] == "prompt"
    model_payload = json.loads(result.model_output or "")
    model_terminal = next(tool for tool in model_payload["tools"] if tool["name"] == "terminal")
    assert all(tool["name"] != removed_terminal_alias for tool in model_payload["tools"])
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
async def test_global_approval_auto_cannot_lower_prompt_command_guard_decision(tmp_path):
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

    approval = next(event for event in reversed(app.runner.event_log.tail(20)) if event["type"] == "approval.decided")
    assert result.is_error is True
    assert result.data["executionStarted"] is False
    assert result.data["approval"]["value"] == "deny"
    assert doomed.exists()
    assert approval["automatic"] is False
    assert approval["policy"] == "prompt"


@pytest.mark.asyncio
async def test_global_approval_auto_cannot_execute_unknown_terminal_command(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    home = tmp_path / "home"
    app = create_app(home=home, provider_name="fake", workspace=workspace)
    fallback = home / "agents" / "agent.yaml"
    raw_fallback = yaml.safe_load(fallback.read_text(encoding="utf-8"))
    raw_fallback["approval"] = {"tools": {"terminal": "auto"}}
    fallback.write_text(yaml.safe_dump(raw_fallback, sort_keys=False), encoding="utf-8")
    app = create_app(home=home, provider_name="fake", workspace=workspace)
    app.approval_runtime.provider = StaticApprovalProvider("deny")
    core = _load_core_with(app, capabilities={"terminal.exec": {"scope": "workspace"}})

    result = await _execute(app, core, "terminal", {"command": "id"})

    approval = next(event for event in reversed(app.runner.event_log.tail(20)) if event["type"] == "approval.decided")
    assert result.is_error is True
    assert result.data["executionStarted"] is False
    assert result.data["approval"]["value"] == "deny"
    assert approval["automatic"] is False
    assert approval["policy"] == "prompt"


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
