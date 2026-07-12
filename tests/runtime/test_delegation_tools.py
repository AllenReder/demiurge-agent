import asyncio
import shutil
from types import SimpleNamespace

import pytest
import yaml

pytestmark = pytest.mark.slow_integration

from demiurge.app import create_app, source_agents_root
from demiurge.providers import LLMResponse, ToolCall
from demiurge.runtime.delegation import subagents_command_text
from demiurge.runtime.scope import PrincipalScopeResolver
from demiurge.runtime.store import RuntimeQuery
from demiurge.sdk import AgentInput, ToolResult, TurnContext
from demiurge.security.capabilities import CapabilityFacade, CapabilitySnapshot
from demiurge.slash import specs_for_surface
from demiurge.tools.registry import BUILTIN_TOOL_DEFINITIONS


CHILD_AGENT_COMPLETION_TIMEOUT = 20


class StaticProvider:
    async def complete(self, request):
        return LLMResponse(content="child done")


class RecordingProvider:
    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.requests = []

    async def complete(self, request):
        self.requests.append(request)
        if self.responses:
            item = self.responses.pop(0)
            if isinstance(item, LLMResponse):
                return item
            return LLMResponse(content=str(item))
        return LLMResponse(content="child done")


class BlockingProvider:
    def __init__(self):
        self.started = 0
        self.release = None

    async def complete(self, request):
        self.started += 1
        if self.release is None:
            self.release = asyncio.Event()
        await self.release.wait()
        return LLMResponse(content="child done")


class LabeledDelegationRuntime:
    def __init__(self, label: str):
        self.label = label

    async def execute(self, call, **kwargs):
        await asyncio.sleep(0)
        return ToolResult(content=self.label, data={"runner": self.label})


def _conversation_scope(app, suffix: str):
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
        core_id="assistant",
        core_revision="rev",
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


def _turn(app, core):
    turn = TurnContext(
        session_id=app.runner.session_id,
        turn_id="turn_delegate",
        core_id=core.core_id,
        core_revision=core.revision,
        user_input=AgentInput(content="delegate", metadata={}),
        metadata={},
    )
    scope = PrincipalScopeResolver(app.runtime_store).admit(app.runner.principal_scope)
    app.task_worker.bind_turn_scope(
        session_id=turn.session_id,
        turn_id=turn.turn_id,
        scope=scope,
    )
    return turn


def _without_default_capability(core, name: str):
    defaults = core.raw_manifest.setdefault("capabilities", {}).setdefault("defaults", {})
    defaults.pop(name, None)
    return core


def _copy_agents(tmp_path):
    target = tmp_path / "agents"
    shutil.copytree(source_agents_root(), target)
    return target


def _write_module(root, rel_path, code):
    path = root / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(code, encoding="utf-8")


def _write_slot(root, rel_path, text=None):
    path = root / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        text
        or "\n".join(
            [
                "entrypoint: module:process",
                "description: test slot",
                "failure_policy: soft",
                "capabilities:",
                "  []",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def _write_pipeline(root, phase, *, serial=None, parallel=None, core_id="assistant"):
    path = root / core_id / "agent" / "pipelines.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw[phase] = {"serial": list(serial or [])}
    if phase != "bootstrap":
        raw[phase]["parallel"] = list(parallel or [])
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")


@pytest.mark.asyncio
async def test_delegate_task_status_and_yield_until_use_runtime_tasks(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    app.runner.provider = StaticProvider()
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    turn = _turn(app, core)
    capability = CapabilityFacade(core)

    delegated = await app.runner.execute_call(
        ToolCall(name="delegate_task", arguments={"goal": "do child work", "core_id": "evolver"}),
        core=core,
        turn=turn,
        capability=capability,
        principal_scope=app.runner.principal_scope,
        emit_event=app.runner.event_log.emit,
    )

    assert delegated.is_error is False
    assert set(delegated.data) == {"task_id"}
    task_id = delegated.data["task_id"]
    status = await app.runner.execute_call(
        ToolCall(name="task_status", arguments={"task_id": task_id}),
        core=core,
        turn=turn,
        capability=capability,
        principal_scope=app.runner.principal_scope,
        emit_event=app.runner.event_log.emit,
    )
    waited = await app.runner.execute_call(
        ToolCall(
            name="yield_until",
            arguments={"task_id": task_id, "timeout_seconds": CHILD_AGENT_COMPLETION_TIMEOUT},
        ),
        core=core,
        turn=turn,
        capability=capability,
        principal_scope=app.runner.principal_scope,
        emit_event=app.runner.event_log.emit,
    )

    assert status.data["task_id"] == task_id
    assert waited.data["status"] == "succeeded"
    assert app.control_plane.read(task_id)["kind"] == "agent.spawn"
    listing = await subagents_command_text(
        app.task_worker,
        principal_scope=app.runner.principal_scope,
        args="",
    )
    detail = await subagents_command_text(
        app.task_worker,
        principal_scope=app.runner.principal_scope,
        args=task_id,
    )
    assert task_id in listing
    assert "Subagent" in detail


@pytest.mark.asyncio
async def test_shared_tool_runtime_uses_the_calling_runner_delegation_adapter(
    tmp_path,
):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    turn_a = _turn(app, core)
    resolver = PrincipalScopeResolver(app.runtime_store)
    issued_b = resolver.local_operator(
        active_session_id="session_runner_b",
        reason="bind second runner delegation adapter",
        allow_unowned_active=True,
    )
    app.session_runtime.create_session(
        session_id="session_runner_b",
        core_id=core.core_id,
        core_revision=core.revision,
        principal_scope=issued_b,
    )
    scope_b = resolver.origin_scope(session_id="session_runner_b")
    runner_b = app.runner.child_agents.host.create_child_runner(
        core_id=core.core_id,
        session_id="session_runner_b",
        principal_scope=scope_b,
    )
    turn_b = TurnContext(
        session_id="session_runner_b",
        turn_id="turn_runner_b",
        core_id=core.core_id,
        core_revision=core.revision,
        user_input=AgentInput(content="inspect"),
    )
    app.runner.delegation_tools = LabeledDelegationRuntime("runner-a")
    runner_b.delegation_tools = LabeledDelegationRuntime("runner-b")

    result_a, result_b = await asyncio.gather(
        app.runner.execute_call(
            ToolCall(name="task_status", arguments={"task_id": "task_a"}),
            core=core,
            turn=turn_a,
            capability=CapabilityFacade(core),
            principal_scope=app.runner.principal_scope,
        ),
        runner_b.execute_call(
            ToolCall(name="task_status", arguments={"task_id": "task_b"}),
            core=core,
            turn=turn_b,
            capability=CapabilityFacade(core),
            principal_scope=scope_b,
        ),
    )

    assert result_a.data == {"runner": "runner-a"}
    assert result_b.data == {"runner": "runner-b"}
    await app.close()


@pytest.mark.asyncio
async def test_task_status_hides_task_owned_by_another_principal_scope(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    scope_a = _conversation_scope(app, "a")
    scope_b = _conversation_scope(app, "b")
    release = asyncio.Event()

    async def blocked_task(_context):
        await release.wait()

    app.task_worker.bind_turn_scope(
        session_id=scope_b.session_id,
        turn_id="turn_b",
        scope=scope_b,
    )
    task = app.task_worker.start_task(
        kind="agent.spawn",
        owner_session_id=scope_b.session_id,
        owner_turn_id="turn_b",
        source_tool="delegate_task",
        task_factory=blocked_task,
        metadata={"private_marker": "session-b-only"},
    )
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    turn_a = TurnContext(
        session_id=scope_a.session_id,
        turn_id="turn_a",
        core_id=core.core_id,
        core_revision=core.revision,
        user_input=AgentInput(content="inspect guessed task"),
    )

    try:
        result = await app.runner.execute_call(
            ToolCall(name="task_status", arguments={"task_id": task.task_id}),
            core=core,
            turn=turn_a,
            capability=CapabilityFacade(core),
            principal_scope=scope_a,
            emit_event=app.runner.event_log.emit,
        )

        assert result.is_error is True
        assert result.content == f"background task not found: {task.task_id}"
        assert "session-b-only" not in result.content
    finally:
        release.set()
        await app.task_worker.drain()
        await app.close()


@pytest.mark.asyncio
async def test_task_status_hides_cross_core_task_owned_by_another_session(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    scope_a = _conversation_scope(app, "cross-core-a")
    resolver = PrincipalScopeResolver(app.runtime_store)
    issued_b = resolver.issue_conversation(
        channel="probe",
        principal_key="user_cross_core_b",
        conversation_key="probe:conversation:cross-core-b",
        session_id="session_cross_core_b",
    )
    app.session_runtime.create_session(
        session_id="session_cross_core_b",
        core_id="evolver",
        core_revision="rev-evolver",
        channel="probe",
        conversation_key="probe:conversation:cross-core-b",
        principal_scope=issued_b,
    )
    scope_b = resolver.conversation(
        channel="probe",
        principal_key="user_cross_core_b",
        conversation_key="probe:conversation:cross-core-b",
        session_id="session_cross_core_b",
    )
    release = asyncio.Event()

    async def blocked_task(_context):
        await release.wait()

    app.task_worker.bind_turn_scope(
        session_id=scope_b.session_id,
        turn_id="turn_cross_core_b",
        scope=scope_b,
    )
    task = app.task_worker.start_task(
        kind="agent.spawn",
        owner_session_id=scope_b.session_id,
        owner_turn_id="turn_cross_core_b",
        source_tool="delegate_task",
        task_factory=blocked_task,
        metadata={"core_id": "evolver", "private_marker": "cross-core-private"},
    )
    core_a = app.core_loader.load(app.version_store.active_core_path("assistant"))
    turn_a = TurnContext(
        session_id=scope_a.session_id,
        turn_id="turn_cross_core_a",
        core_id=core_a.core_id,
        core_revision=core_a.revision,
        user_input=AgentInput(content="inspect cross-core task"),
    )

    try:
        result = await app.runner.execute_call(
            ToolCall(name="task_status", arguments={"task_id": task.task_id}),
            core=core_a,
            turn=turn_a,
            capability=CapabilityFacade(core_a),
            principal_scope=scope_a,
            emit_event=app.runner.event_log.emit,
        )

        assert result.is_error is True
        assert result.content == f"background task not found: {task.task_id}"
        assert "cross-core-private" not in result.content
        audits = app.runtime_store.query(
            RuntimeQuery(
                table="runtime_events",
                where={"type": "principal_scope.owner_lookup_denied"},
                order_by="seq",
                limit=10,
            )
        ).rows
        assert any(
            event["payload"] == {
                "table": "tasks",
                "lookup_field": "task_id",
                "lookup_id": task.task_id,
                "lookup_id_truncated": False,
                "reason": "not_authorized",
            }
            for event in audits
        )
    finally:
        release.set()
        await app.task_worker.drain()
        await app.close()


@pytest.mark.asyncio
async def test_model_task_status_cannot_request_operator_or_debug_log_view(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    scope = _conversation_scope(app, "model-view")
    release = asyncio.Event()

    async def blocked_task(_context):
        await release.wait()

    app.task_worker.bind_turn_scope(
        session_id=scope.session_id,
        turn_id="turn_model_view",
        scope=scope,
    )
    task = app.task_worker.start_task(
        kind="agent.spawn",
        owner_session_id=scope.session_id,
        owner_turn_id="turn_model_view",
        source_tool="delegate_task",
        task_factory=blocked_task,
        metadata={"private_token": "task-metadata-must-stay-operator-only"},
    )
    private_log = "operator-only-full-task-log"
    app.task_worker.append_log(task.task_id, private_log)
    app.task_worker.set_summary(task.task_id, "model-visible-summary-" + ("x" * 5000))
    app.task_worker.set_result_ref(task.task_id, "file:///operator/private/result.json")
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    turn = TurnContext(
        session_id=scope.session_id,
        turn_id="turn_model_view",
        core_id=core.core_id,
        core_revision=core.revision,
        user_input=AgentInput(content="inspect task"),
    )

    try:
        for requested_view in ("operator", "debug"):
            result = await app.runner.execute_call(
                ToolCall(
                    name="task_status",
                    arguments={"task_id": task.task_id, "view": requested_view},
                ),
                core=core,
                turn=turn,
                capability=CapabilityFacade(core),
                principal_scope=scope,
                emit_event=app.runner.event_log.emit,
            )

            assert result.is_error is False
            assert set(result.data) == {
                "task_id",
                "kind",
                "status",
                "running",
                "started_at",
                "completed_at",
                "summary",
            }
            assert "log" not in result.data
            assert private_log not in result.content
            assert "task-metadata-must-stay-operator-only" not in result.content
            assert "operator/private/result.json" not in result.content
            assert len(result.data["summary"]) <= 1200

        schema = BUILTIN_TOOL_DEFINITIONS["task_status"].input_schema
        assert "view" not in schema["properties"]

        timed_out = await app.runner.execute_call(
            ToolCall(
                name="yield_until",
                arguments={"task_id": task.task_id, "timeout_seconds": 0},
            ),
            core=core,
            turn=turn,
            capability=CapabilityFacade(core),
            principal_scope=scope,
            emit_event=app.runner.event_log.emit,
        )
        assert timed_out.data["timed_out"] is True
        assert "log" not in timed_out.data
        assert "log_tail" not in timed_out.data
        assert private_log not in timed_out.content

        cancelled = await app.runner.execute_call(
            ToolCall(
                name="task_control",
                arguments={"task_id": task.task_id, "command": "cancel"},
            ),
            core=core,
            turn=turn,
            capability=CapabilityFacade(core),
            principal_scope=scope,
            emit_event=app.runner.event_log.emit,
        )
        assert cancelled.data["status"] == "cancelled"
        assert "log" not in cancelled.data
        assert "log_tail" not in cancelled.data
        assert private_log not in cancelled.content

        waited = await app.runner.execute_call(
            ToolCall(
                name="yield_until",
                arguments={"task_id": task.task_id, "timeout_seconds": 1},
            ),
            core=core,
            turn=turn,
            capability=CapabilityFacade(core),
            principal_scope=scope,
            emit_event=app.runner.event_log.emit,
        )
        assert waited.data["status"] == "cancelled"
        assert "log" not in waited.data
        assert "log_tail" not in waited.data
        assert private_log not in waited.content
    finally:
        release.set()
        await app.task_worker.drain()
        await app.close()


@pytest.mark.asyncio
async def test_delegation_tool_uses_shared_execution_identity_validation(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    scope = _conversation_scope(app, "shared-scope-validation")
    release = asyncio.Event()

    async def blocked_task(_context):
        await release.wait()

    app.task_worker.bind_turn_scope(
        session_id=scope.session_id,
        turn_id="turn_shared_scope",
        scope=scope,
    )
    task = app.task_worker.start_task(
        kind="agent.spawn",
        owner_session_id=scope.session_id,
        owner_turn_id="turn_shared_scope",
        source_tool="delegate_task",
        task_factory=blocked_task,
    )
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    turn = TurnContext(
        session_id=scope.session_id,
        turn_id="turn_shared_scope",
        core_id=core.core_id,
        core_revision=core.revision,
        user_input=AgentInput(content="inspect task"),
    )
    snapshot = CapabilitySnapshot.capture(core)
    capability = CapabilityFacade(core, snapshot=snapshot)
    forged_context = SimpleNamespace(
        session_id=scope.session_id,
        principal_scope=scope,
        core_id="evolver",
        core_revision=core.revision,
        capability_snapshot=snapshot,
        cancellation=SimpleNamespace(turn_id=turn.turn_id),
        admission_lease=SimpleNamespace(
            turn_id=turn.turn_id,
            session_id=scope.session_id,
        ),
        trace_id=turn.turn_id,
    )

    try:
        result = await app.runner.execute_call(
            ToolCall(name="task_status", arguments={"task_id": task.task_id}),
            core=core,
            turn=turn,
            capability=capability,
            execution_context=forged_context,
            emit_event=app.runner.event_log.emit,
        )

        assert result.is_error is True
        assert result.data == {"executionStarted": False}
        assert result.content == "TurnExecutionContext does not match tool execution identity"
    finally:
        release.set()
        await app.task_worker.drain()
        await app.close()


@pytest.mark.asyncio
async def test_scheduler_scope_cannot_control_task_from_another_origin_session(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    resolver = PrincipalScopeResolver(app.runtime_store)
    scheduler_scope = resolver.scheduled_run(
        core_id="assistant",
        schedule_id="daily:summary",
        run_id="run_scheduler_owner",
        session_id="session_scheduler_owner",
    )
    app.session_runtime.create_session(
        session_id=scheduler_scope.session_id,
        core_id="assistant",
        core_revision="rev",
        principal_scope=scheduler_scope,
    )
    scheduler_scope = resolver.origin_scope(session_id=scheduler_scope.session_id)
    other_scope = _conversation_scope(app, "scheduler-other")
    release = asyncio.Event()

    async def blocked_task(_context):
        await release.wait()

    app.task_worker.bind_turn_scope(
        session_id=other_scope.session_id,
        turn_id="turn_scheduler_other",
        scope=other_scope,
    )
    other_task = app.task_worker.start_task(
        kind="agent.spawn",
        owner_session_id=other_scope.session_id,
        owner_turn_id="turn_scheduler_other",
        source_tool="delegate_task",
        task_factory=blocked_task,
    )

    try:
        with pytest.raises(KeyError):
            app.task_worker.get_owned(scheduler_scope, other_task.task_id)
        with pytest.raises(KeyError):
            await app.task_worker.cancel_owned(scheduler_scope, other_task.task_id)
        assert app.task_worker.get(other_task.task_id).status in {"queued", "running"}
    finally:
        release.set()
        await app.task_worker.drain()
        await app.close()


@pytest.mark.asyncio
async def test_delegated_child_scope_controls_only_child_owned_tasks(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    resolver = PrincipalScopeResolver(app.runtime_store)
    parent_scope = _conversation_scope(app, "child-parent")
    child_scope = resolver.delegated_agent(
        parent=parent_scope,
        task_id="task_spawn_child",
        parent_turn_id="turn_parent",
        child_session_id="session_child_owned",
    )
    app.session_runtime.create_session(
        session_id=child_scope.session_id,
        core_id="assistant",
        core_revision="rev",
        principal_scope=child_scope,
    )
    child_scope = resolver.origin_scope(session_id=child_scope.session_id)
    release = asyncio.Event()

    async def blocked_task(_context):
        await release.wait()

    app.task_worker.bind_turn_scope(
        session_id=parent_scope.session_id,
        turn_id="turn_parent_task",
        scope=parent_scope,
    )
    parent_task = app.task_worker.start_task(
        kind="agent.spawn",
        owner_session_id=parent_scope.session_id,
        owner_turn_id="turn_parent_task",
        source_tool="delegate_task",
        task_factory=blocked_task,
    )
    app.task_worker.bind_turn_scope(
        session_id=child_scope.session_id,
        turn_id="turn_child_task",
        scope=child_scope,
    )
    child_task = app.task_worker.start_task(
        kind="agent.spawn",
        owner_session_id=child_scope.session_id,
        owner_turn_id="turn_child_task",
        source_tool="delegate_task",
        task_factory=blocked_task,
    )

    try:
        assert app.task_worker.get_owned(child_scope, child_task.task_id).task_id == child_task.task_id
        with pytest.raises(KeyError):
            app.task_worker.get_owned(child_scope, parent_task.task_id)
        with pytest.raises(KeyError):
            await app.task_worker.cancel_owned(child_scope, parent_task.task_id)
        assert app.task_worker.get(parent_task.task_id).status in {"queued", "running"}
    finally:
        release.set()
        await app.task_worker.drain()
        await app.close()


@pytest.mark.asyncio
async def test_explicit_operator_scope_can_control_normally_owned_task(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    resolver = PrincipalScopeResolver(app.runtime_store)
    conversation_scope = _conversation_scope(app, "operator-target")
    operator_scope = resolver.local_operator(
        active_session_id=app.runner.session_id,
        reason="operator task administration regression",
    )
    release = asyncio.Event()

    async def blocked_task(_context):
        await release.wait()

    app.task_worker.bind_turn_scope(
        session_id=conversation_scope.session_id,
        turn_id="turn_operator_target",
        scope=conversation_scope,
    )
    task = app.task_worker.start_task(
        kind="agent.spawn",
        owner_session_id=conversation_scope.session_id,
        owner_turn_id="turn_operator_target",
        source_tool="delegate_task",
        task_factory=blocked_task,
    )

    try:
        assert app.task_worker.get_owned(operator_scope, task.task_id).task_id == task.task_id
        cancelled = await app.task_worker.cancel_owned(operator_scope, task.task_id)
        assert cancelled.status == "cancelled"
    finally:
        release.set()
        await app.task_worker.drain()
        await app.close()


@pytest.mark.asyncio
async def test_task_control_cannot_cancel_task_owned_by_another_principal_scope(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    scope_a = _conversation_scope(app, "a")
    scope_b = _conversation_scope(app, "b")
    release = asyncio.Event()

    async def blocked_task(_context):
        await release.wait()

    app.task_worker.bind_turn_scope(
        session_id=scope_b.session_id,
        turn_id="turn_b",
        scope=scope_b,
    )
    task = app.task_worker.start_task(
        kind="agent.spawn",
        owner_session_id=scope_b.session_id,
        owner_turn_id="turn_b",
        source_tool="delegate_task",
        task_factory=blocked_task,
    )
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    turn_a = TurnContext(
        session_id=scope_a.session_id,
        turn_id="turn_a",
        core_id=core.core_id,
        core_revision=core.revision,
        user_input=AgentInput(content="cancel guessed task"),
    )

    try:
        result = await app.runner.execute_call(
            ToolCall(
                name="task_control",
                arguments={"task_id": task.task_id, "command": "cancel"},
            ),
            core=core,
            turn=turn_a,
            capability=CapabilityFacade(core),
            principal_scope=scope_a,
            emit_event=app.runner.event_log.emit,
        )

        assert result.is_error is True
        assert result.content == f"background task not found: {task.task_id}"
        assert app.task_worker.get(task.task_id).status in {"queued", "running"}
    finally:
        release.set()
        await app.task_worker.drain()
        await app.close()


@pytest.mark.asyncio
async def test_yield_until_cannot_wait_for_task_owned_by_another_principal_scope(
    tmp_path,
):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    scope_a = _conversation_scope(app, "a")
    scope_b = _conversation_scope(app, "b")
    release = asyncio.Event()

    async def blocked_task(_context):
        await release.wait()

    app.task_worker.bind_turn_scope(
        session_id=scope_b.session_id,
        turn_id="turn_b",
        scope=scope_b,
    )
    task = app.task_worker.start_task(
        kind="agent.spawn",
        owner_session_id=scope_b.session_id,
        owner_turn_id="turn_b",
        source_tool="delegate_task",
        task_factory=blocked_task,
    )
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    turn_a = TurnContext(
        session_id=scope_a.session_id,
        turn_id="turn_a",
        core_id=core.core_id,
        core_revision=core.revision,
        user_input=AgentInput(content="wait for guessed task"),
    )

    try:
        result = await app.runner.execute_call(
            ToolCall(
                name="yield_until",
                arguments={"task_id": task.task_id, "timeout_seconds": 0},
            ),
            core=core,
            turn=turn_a,
            capability=CapabilityFacade(core),
            principal_scope=scope_a,
            emit_event=app.runner.event_log.emit,
        )

        assert result.is_error is True
        assert result.content == f"background task not found: {task.task_id}"
        assert app.task_worker.get(task.task_id).status in {"queued", "running"}
    finally:
        release.set()
        await app.task_worker.drain()
        await app.close()


@pytest.mark.asyncio
async def test_subagents_commands_hide_and_cannot_cancel_another_principal_task(
    tmp_path,
):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    scope_a = _conversation_scope(app, "a")
    scope_b = _conversation_scope(app, "b")
    release = asyncio.Event()

    async def blocked_task(_context):
        await release.wait()

    app.task_worker.bind_turn_scope(
        session_id=scope_b.session_id,
        turn_id="turn_b",
        scope=scope_b,
    )
    task = app.task_worker.start_task(
        kind="agent.spawn",
        owner_session_id=scope_b.session_id,
        owner_turn_id="turn_b",
        source_tool="delegate_task",
        task_factory=blocked_task,
        metadata={"private_marker": "session-b-only"},
    )

    try:
        listing = await subagents_command_text(
            app.task_worker,
            principal_scope=scope_a,
            args="",
        )
        detail = await subagents_command_text(
            app.task_worker,
            principal_scope=scope_a,
            args=task.task_id,
        )
        cancelled = await subagents_command_text(
            app.task_worker,
            principal_scope=scope_a,
            args=f"cancel {task.task_id}",
        )

        assert task.task_id not in listing
        assert detail == f"Subagent task not found: {task.task_id}"
        assert cancelled == f"Subagent task not found: {task.task_id}"
        assert "session-b-only" not in detail
        assert app.task_worker.get(task.task_id).status in {"queued", "running"}
    finally:
        release.set()
        await app.task_worker.drain()
        await app.close()


@pytest.mark.asyncio
async def test_delegate_task_defaults_to_base_slots_and_records_metadata(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    app.runner.provider = StaticProvider()
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    turn = _turn(app, core)
    capability = CapabilityFacade(core)

    delegated = await app.runner.execute_call(
        ToolCall(name="delegate_task", arguments={"goal": "do child work", "core_id": "evolver"}),
        core=core,
        turn=turn,
        capability=capability,
        emit_event=app.runner.event_log.emit,
    )
    await app.task_worker.wait(delegated.data["task_id"], timeout_seconds=CHILD_AGENT_COMPLETION_TIMEOUT)

    record = app.task_worker.get(delegated.data["task_id"])
    assert record.metadata["requested_child_agent_slots"] == {
        "input_slots": ["base_input"],
        "output_slots": ["base_output"],
        "use_bootstrap": False,
    }
    assert record.metadata["resolved_child_agent_slots"] == {
        "input_slots": {"serial": ["base_input"], "parallel": []},
        "output_slots": {"serial": ["base_output"], "parallel": []},
        "use_bootstrap": False,
    }
    assert record.metadata["requested_child_agent_tools"] == "all"
    assert "read_file" in record.metadata["resolved_child_agent_tools"]["resolved"]


@pytest.mark.asyncio
async def test_delegate_task_all_slots_preserves_child_pipeline_metadata(tmp_path):
    agents = _copy_agents(tmp_path)
    _write_module(
        agents,
        "evolver/agent/output/child_extra/module.py",
        "def process(ctx):\n"
        "    ctx.output.send_text('child-extra')\n",
    )
    _write_slot(agents, "evolver/agent/output/child_extra/slot.yaml")
    _write_pipeline(agents, "output", serial=["base_output", "child_extra"], core_id="evolver")
    app = create_app(home=tmp_path / "home", provider_name="fake", agents_root=agents)
    app.runner.provider = StaticProvider()
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    turn = _turn(app, core)
    capability = CapabilityFacade(core)

    delegated = await app.runner.execute_call(
        ToolCall(
            name="delegate_task",
            arguments={
                "goal": "do child work",
                "core_id": "evolver",
                "input_slots": "all",
                "output_slots": "all",
            },
        ),
        core=core,
        turn=turn,
        capability=capability,
        emit_event=app.runner.event_log.emit,
    )
    await app.task_worker.wait(delegated.data["task_id"], timeout_seconds=CHILD_AGENT_COMPLETION_TIMEOUT)

    record = app.task_worker.get(delegated.data["task_id"])
    assert record.metadata["requested_child_agent_slots"] == {
        "input_slots": "all",
        "output_slots": "all",
        "use_bootstrap": False,
    }
    assert record.metadata["resolved_child_agent_slots"]["output_slots"] == {
        "serial": ["base_output", "child_extra"],
        "parallel": [],
    }


@pytest.mark.asyncio
async def test_delegate_task_returns_tool_error_for_invalid_child_slot_selection(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    turn = _turn(app, core)
    capability = CapabilityFacade(core)

    result = await app.runner.execute_call(
        ToolCall(
            name="delegate_task",
            arguments={"goal": "do child work", "core_id": "evolver", "input_slots": ["missing"]},
        ),
        core=core,
        turn=turn,
        capability=capability,
        emit_event=app.runner.event_log.emit,
    )

    assert result.is_error is True
    assert "unknown input slot id: missing" in result.content


def test_delegate_task_schema_exposes_tools_selection_not_tool_policy():
    schema = BUILTIN_TOOL_DEFINITIONS["delegate_task"].input_schema
    properties = schema["properties"]

    assert "tool_policy" not in properties
    assert properties["tools"] == {
        "anyOf": [
            {"type": "string", "enum": ["all", "none"]},
            {"type": "array", "items": {"type": "string"}},
        ],
        "default": "all",
    }


@pytest.mark.asyncio
async def test_delegate_task_returns_tool_error_for_invalid_child_tool_selection(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    turn = _turn(app, core)
    capability = CapabilityFacade(core)

    result = await app.runner.execute_call(
        ToolCall(
            name="delegate_task",
            arguments={"goal": "do child work", "core_id": "evolver", "tools": ["missing"]},
        ),
        core=core,
        turn=turn,
        capability=capability,
        emit_event=app.runner.event_log.emit,
    )

    assert result.is_error is True
    assert "unknown child tool id: missing" in result.content


@pytest.mark.asyncio
async def test_delegate_task_rejects_legacy_tool_policy_argument(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    turn = _turn(app, core)
    capability = CapabilityFacade(core)

    result = await app.runner.execute_call(
        ToolCall(
            name="delegate_task",
            arguments={"goal": "do child work", "core_id": "evolver", "tool_policy": {"deny": ["read_file"]}},
        ),
        core=core,
        turn=turn,
        capability=capability,
        emit_event=app.runner.event_log.emit,
    )

    assert result.is_error is True
    assert "tool_policy is not supported; use tools" in result.content


@pytest.mark.asyncio
async def test_delegate_task_passes_child_tool_selection_to_spawned_task(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    provider = RecordingProvider()
    app.runner.provider = provider
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    turn = _turn(app, core)
    capability = CapabilityFacade(core)

    delegated = await app.runner.execute_call(
        ToolCall(
            name="delegate_task",
            arguments={"goal": "do child work", "core_id": "evolver", "tools": ["read_file"]},
        ),
        core=core,
        turn=turn,
        capability=capability,
        emit_event=app.runner.event_log.emit,
    )
    await app.task_worker.wait(delegated.data["task_id"], timeout_seconds=CHILD_AGENT_COMPLETION_TIMEOUT)

    record = app.task_worker.get(delegated.data["task_id"])
    assert record.metadata["requested_child_agent_tools"] == ["read_file"]
    assert record.metadata["resolved_child_agent_tools"] == {
        "requested": ["read_file"],
        "resolved": ["read_file"],
    }
    assert [tool.name for tool in provider.requests[0].tools] == ["read_file"]


@pytest.mark.asyncio
async def test_runtime_task_wait_can_consume_existing_completion_event(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")

    async def task(ctx):
        ctx.append_log("finished")
        return "worker done"

    record = app.task_worker.start_task(
        kind="terminal.exec",
        owner_session_id=app.runner.session_id,
        owner_turn_id="turn_origin",
        source_tool="test",
        task_factory=task,
    )

    await app.task_worker.wait(record.task_id, timeout_seconds=1)
    assert [event.task_id for event in app.task_worker.pending_events_for_session(app.runner.session_id)] == [
        record.task_id
    ]

    waited = await app.task_worker.wait(record.task_id, timeout_seconds=1, consume_completion=True)

    assert waited.status == "succeeded"
    assert app.task_worker.pending_events_for_session(app.runner.session_id) == []


@pytest.mark.asyncio
async def test_yield_until_timeout_returns_running_status_without_tool_error(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    provider = BlockingProvider()
    app.runner.provider = provider
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    turn = _turn(app, core)
    capability = CapabilityFacade(core)

    delegated = await app.runner.execute_call(
        ToolCall(name="delegate_task", arguments={"goal": "do slow child work", "core_id": "evolver"}),
        core=core,
        turn=turn,
        capability=capability,
        emit_event=app.runner.event_log.emit,
    )
    task_id = delegated.data["task_id"]
    for _ in range(50):
        if provider.started and provider.release is not None:
            break
        await asyncio.sleep(0.01)
    assert provider.release is not None

    waited = await app.runner.execute_call(
        ToolCall(name="yield_until", arguments={"task_id": task_id, "timeout_seconds": 0.01}),
        core=core,
        turn=turn,
        capability=capability,
        emit_event=app.runner.event_log.emit,
    )

    assert waited.is_error is False
    assert waited.data["task_id"] == task_id
    assert waited.data["status"] == "running"
    assert waited.data["running"] is True
    assert waited.data["timed_out"] is True
    provider.release.set()
    await app.runner.background_tasks.drain()
    assert [event.task_id for event in app.task_worker.pending_events_for_session(app.runner.session_id)] == [
        task_id
    ]


@pytest.mark.asyncio
async def test_delegation_tools_require_capabilities(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    turn = _turn(app, core)

    no_spawn = _without_default_capability(core, "agents.spawn:evolver")
    spawn = await app.runner.execute_call(
        ToolCall(name="delegate_task", arguments={"goal": "do child work", "core_id": "evolver"}),
        core=no_spawn,
        turn=turn,
        capability=CapabilityFacade(no_spawn),
        emit_event=app.runner.event_log.emit,
    )

    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    no_task_control = _without_default_capability(core, "task.control")
    status = await app.runner.execute_call(
        ToolCall(name="task_status", arguments={"task_id": "task_missing"}),
        core=no_task_control,
        turn=turn,
        capability=CapabilityFacade(no_task_control),
        emit_event=app.runner.event_log.emit,
    )
    control = await app.runner.execute_call(
        ToolCall(name="task_control", arguments={"task_id": "task_missing"}),
        core=no_task_control,
        turn=turn,
        capability=CapabilityFacade(no_task_control),
        emit_event=app.runner.event_log.emit,
    )
    waited = await app.runner.execute_call(
        ToolCall(name="yield_until", arguments={"task_id": "task_missing"}),
        core=no_task_control,
        turn=turn,
        capability=CapabilityFacade(no_task_control),
        emit_event=app.runner.event_log.emit,
    )

    assert spawn.is_error is True
    assert "capability denied: agents.spawn:evolver" in spawn.content
    assert status.is_error is True
    assert control.is_error is True
    assert waited.is_error is True
    assert "capability denied: task.control" in status.content
    assert "capability denied: task.control" in control.content
    assert "capability denied: task.control" in waited.content


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["retry", "handoff", "mute", "notify"])
async def test_task_control_rejects_unsupported_commands(tmp_path, command):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    turn = _turn(app, core)
    capability = CapabilityFacade(core)

    result = await app.runner.execute_call(
        ToolCall(name="task_control", arguments={"task_id": "task_missing", "command": command}),
        core=core,
        turn=turn,
        capability=capability,
        emit_event=app.runner.event_log.emit,
    )

    assert result.is_error is True
    assert f"unsupported task_control command: {command}" in result.content


@pytest.mark.asyncio
async def test_delegate_task_silent_notify_policy_suppresses_completion_event(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    app.runner.provider = StaticProvider()
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    turn = _turn(app, core)
    capability = CapabilityFacade(core)

    delegated = await app.runner.execute_call(
        ToolCall(
            name="delegate_task",
            arguments={"goal": "do quiet child work", "core_id": "evolver", "notify_policy": "silent"},
        ),
        core=core,
        turn=turn,
        capability=capability,
        emit_event=app.runner.event_log.emit,
    )
    await app.task_worker.wait(delegated.data["task_id"], timeout_seconds=CHILD_AGENT_COMPLETION_TIMEOUT)

    assert delegated.is_error is False
    assert set(delegated.data) == {"task_id"}
    assert app.task_worker.pending_events_for_session(app.runner.session_id) == []


@pytest.mark.asyncio
async def test_delegate_task_rejects_unknown_notify_policy(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    turn = _turn(app, core)
    capability = CapabilityFacade(core)

    result = await app.runner.execute_call(
        ToolCall(
            name="delegate_task",
            arguments={"goal": "do child work", "core_id": "evolver", "notify_policy": "notify"},
        ),
        core=core,
        turn=turn,
        capability=capability,
        emit_event=app.runner.event_log.emit,
    )

    assert result.is_error is True
    assert "unsupported notify_policy" in result.content


@pytest.mark.asyncio
async def test_delegate_task_allows_two_concurrent_children(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    provider = BlockingProvider()
    app.runner.provider = provider
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    turn = _turn(app, core)
    capability = CapabilityFacade(core)

    first = await app.runner.execute_call(
        ToolCall(name="delegate_task", arguments={"goal": "first child", "core_id": "evolver"}),
        core=core,
        turn=turn,
        capability=capability,
        emit_event=app.runner.event_log.emit,
    )
    second = await app.runner.execute_call(
        ToolCall(name="delegate_task", arguments={"goal": "second child", "core_id": "evolver"}),
        core=core,
        turn=turn,
        capability=capability,
        emit_event=app.runner.event_log.emit,
    )

    assert set(first.data) == {"task_id"}
    assert set(second.data) == {"task_id"}
    assert len(app.task_worker.list_tasks(owner_session_id=turn.session_id, kind="agent.spawn", include_completed=False)) == 2
    for _ in range(50):
        if provider.started >= 2 and provider.release is not None:
            break
        await asyncio.sleep(0.01)
    assert provider.started == 2
    assert provider.release is not None
    provider.release.set()
    await app.runner.background_tasks.drain()


@pytest.mark.asyncio
async def test_terminal_background_true_returns_runtime_task(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake", workspace=tmp_path)
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    turn = _turn(app, core)
    capability = CapabilityFacade(core)

    result = await app.runner.execute_call(
        ToolCall(name="terminal", arguments={"command": "printf ok", "background": True}),
        core=core,
        turn=turn,
        capability=capability,
        principal_scope=app.task_worker.scope_for_turn(
            session_id=turn.session_id,
            turn_id=turn.turn_id,
        ),
        emit_event=app.runner.event_log.emit,
    )

    assert result.is_error is False
    assert set(result.data) == {"task_id"}
    assert result.data["task_id"].startswith("task_")
    await app.task_worker.wait(result.data["task_id"], timeout_seconds=2)
    assert app.control_plane.read(result.data["task_id"])["kind"] == "terminal.exec"


@pytest.mark.asyncio
async def test_delegate_task_rejects_depth_excess_and_accepts_tool_selection(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    turn = _turn(app, core)
    turn.metadata["delegation_depth"] = 2
    capability = CapabilityFacade(core)

    too_deep = await app.runner.execute_call(
        ToolCall(name="delegate_task", arguments={"goal": "nested", "core_id": "evolver"}),
        core=core,
        turn=turn,
        capability=capability,
        emit_event=app.runner.event_log.emit,
    )
    tool_selection = await app.runner.execute_call(
        ToolCall(
            name="delegate_task",
            arguments={"goal": "nested", "core_id": "evolver", "tools": "none"},
        ),
        core=core,
        turn=_turn(app, core),
        capability=capability,
        emit_event=app.runner.event_log.emit,
    )

    assert too_deep.is_error is True
    assert "max_depth=2" in too_deep.content
    assert tool_selection.is_error is False
    record = app.task_worker.get(tool_selection.data["task_id"])
    assert record.metadata["requested_child_agent_tools"] == "none"


@pytest.mark.asyncio
async def test_tool_policy_denies_child_tool_execution(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    turn = _turn(app, core)
    turn.metadata["tool_policy"] = {"deny": ["read_file"]}
    capability = CapabilityFacade(core)

    result = await app.runner.execute_call(
        ToolCall(name="read_file", arguments={}),
        core=core,
        turn=turn,
        capability=capability,
        principal_scope=app.task_worker.scope_for_turn(
            session_id=turn.session_id,
            turn_id=turn.turn_id,
        ),
        emit_event=app.runner.event_log.emit,
    )

    assert result.is_error is True
    assert "not allowed" in result.content


def test_subagents_slash_command_is_available_on_tui_and_telegram():
    assert "subagents" in {spec.name for spec in specs_for_surface("tui")}
    assert "subagents" in {spec.name for spec in specs_for_surface("telegram")}
