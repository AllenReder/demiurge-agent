from dataclasses import FrozenInstanceError

import pytest
import yaml

from demiurge.app import create_app
from demiurge.providers import LLMResponse, ToolCall
from demiurge.runtime.scope import PrincipalScopeResolver
from demiurge.sdk import AgentInput, ToolResult, TurnContext
from demiurge.security.capabilities import CapabilityFacade
from demiurge.tools.registry import (
    EffectRequest,
    EffectResult,
    ResolvedEffectCatalog,
    ResolvedEffectEntry,
    ToolRegistryCollisionError,
)


def _turn(
    core,
    suffix: str,
    *,
    core_revision: str | None = None,
    metadata: dict | None = None,
) -> TurnContext:
    return TurnContext(
        session_id=f"session_{suffix}",
        turn_id=f"turn_{suffix}",
        core_id=core.core_id,
        core_revision=core_revision or core.revision,
        user_input=AgentInput(content="probe"),
        metadata=dict(metadata or {}),
    )


def _operator_scope(app, core, turn: TurnContext):
    resolver = PrincipalScopeResolver(app.runtime_store)
    issued = resolver.local_operator(
        active_session_id=turn.session_id,
        reason="bind resolved effect test session",
        allow_unowned_active=True,
    )
    app.session_runtime.create_session(
        session_id=turn.session_id,
        core_id=core.core_id,
        core_revision=turn.core_revision,
        principal_scope=issued,
    )
    return resolver.origin_scope(session_id=turn.session_id)


def test_registry_rejects_builtin_authored_name_collision_with_provenance():
    entries = (
        ResolvedEffectEntry(
            name="read_file",
            description="Builtin read",
            input_schema={"type": "object"},
            source="builtin",
            core_id="assistant",
            core_revision="rev",
            adapter_key="builtin:read_file",
            provenance="builtin:read_file",
            _adapter="read_file",
        ),
        ResolvedEffectEntry(
            name="read_file",
            description="Authored read",
            input_schema={"type": "object"},
            source="authored",
            core_id="assistant",
            core_revision="rev",
            adapter_key="authored:agent/tools/read_file",
            provenance="authored:agent/tools/read_file",
            _adapter=object(),
        ),
    )

    with pytest.raises(ToolRegistryCollisionError) as exc_info:
        ResolvedEffectCatalog(
            core_id="assistant",
            core_revision="rev",
            entries=entries,
        )

    message = str(exc_info.value)
    assert "tool name collision: read_file" in message
    assert "builtin:read_file" in message
    assert "authored:agent/tools/read_file" in message


def test_resolved_effect_entries_are_immutable():
    entry = ResolvedEffectEntry(
        name="read_file",
        description="Builtin read",
        input_schema={"type": "object"},
        source="builtin",
        core_id="assistant",
        core_revision="rev",
        adapter_key="builtin:read_file",
        provenance="builtin:read_file",
        _adapter="read_file",
    )

    with pytest.raises(FrozenInstanceError):
        entry.approval_policy = "deny"


def test_effect_request_rejects_an_entry_not_owned_by_its_catalog():
    catalog_entry = ResolvedEffectEntry(
        name="read_file",
        description="Builtin read",
        input_schema={"type": "object"},
        source="builtin",
        core_id="assistant",
        core_revision="rev",
        adapter_key="builtin:read_file",
        provenance="builtin:read_file",
        _adapter="read_file",
    )
    catalog = ResolvedEffectCatalog(
        core_id="assistant",
        core_revision="rev",
        entries=(catalog_entry,),
    )
    forged_entry = ResolvedEffectEntry(
        name="read_file",
        description="Forged read",
        input_schema={"type": "object"},
        source="authored",
        core_id="assistant",
        core_revision="rev",
        adapter_key="authored:agent/tools/read_file",
        provenance="authored:agent/tools/read_file",
        _adapter=object(),
    )

    with pytest.raises(ValueError, match="not owned by resolved effect catalog"):
        EffectRequest(
            entry=forged_entry,
            name="read_file",
            arguments={},
            call_id="call_forged",
            origin="model",
            catalog=catalog,
        )


def test_resolved_entry_deep_freezes_schema_and_returns_detached_definitions():
    schema = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {"type": "string", "enum": ["alpha"]},
            }
        },
    }
    entry = ResolvedEffectEntry(
        name="probe",
        description="Probe",
        input_schema=schema,
        source="builtin",
        core_id="assistant",
        core_revision="rev",
        adapter_key="builtin:probe",
        provenance="builtin:probe",
        _adapter="probe",
    )

    schema["properties"]["items"]["items"]["enum"].append("mutated")
    first = entry.to_definition().input_schema
    first["properties"]["items"]["items"]["enum"].append("detached")
    second = entry.to_definition().input_schema

    assert second["properties"]["items"]["items"]["enum"] == ["alpha"]


def test_effect_request_deep_freezes_arguments_and_thaws_a_detached_tool_call():
    entry = ResolvedEffectEntry(
        name="probe",
        description="Probe",
        input_schema={"type": "object"},
        source="builtin",
        core_id="assistant",
        core_revision="rev",
        adapter_key="builtin:probe",
        provenance="builtin:probe",
        _adapter="probe",
    )
    catalog = ResolvedEffectCatalog(
        core_id="assistant",
        core_revision="rev",
        entries=(entry,),
    )
    arguments = {"nested": {"items": ["alpha"]}}
    request = catalog.request_for(
        ToolCall(name="probe", arguments=arguments, id="call_probe")
    )
    assert request is not None

    arguments["nested"]["items"].append("mutated")
    call = request.to_tool_call()
    call.arguments["nested"]["items"].append("detached")

    assert request.to_tool_call().arguments == {"nested": {"items": ["alpha"]}}


def test_effect_result_normalizes_success_denial_invalid_and_failed_results():
    entry = ResolvedEffectEntry(
        name="probe",
        description="Probe",
        input_schema={"type": "object"},
        source="builtin",
        core_id="assistant",
        core_revision="rev",
        adapter_key="builtin:probe",
        provenance="builtin:probe",
        _adapter="probe",
    )

    succeeded = EffectResult.normalize(entry, ToolResult(content="ok"))
    denied = EffectResult.normalize(
        entry,
        ToolResult(
            content="approval denied: probe",
            is_error=True,
            data={
                "executionStarted": False,
                "approval": {"value": "deny"},
            },
        ),
    )
    invalid = EffectResult.normalize(
        entry,
        ToolResult(
            content="invalid arguments",
            is_error=True,
            data={"executionStarted": False},
        ),
    )
    failed = EffectResult.normalize(
        entry,
        ToolResult(
            content="adapter failed",
            is_error=True,
            data={"executionStarted": True},
        ),
    )
    capability_denied = EffectResult.normalize(
        entry,
        ToolResult(
            content="capability denied: probe.effect",
            is_error=True,
            data={
                "executionStarted": False,
                "denial": "capability",
            },
        ),
    )

    assert succeeded.status == "succeeded"
    assert succeeded.error is None
    assert denied.status == "denied"
    assert denied.error is not None and denied.error.execution_started is False
    assert invalid.status == "invalid"
    assert capability_denied.status == "denied"
    assert failed.status == "failed"
    assert failed.error is not None and failed.error.execution_started is True


@pytest.mark.asyncio
async def test_tool_runtime_rejects_bare_tool_calls(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    turn = _turn(core, "bare_call")
    principal_scope = _operator_scope(app, core, turn)

    with pytest.raises(TypeError, match="requires an EffectRequest"):
        await app.tool_runtime.execute(
            ToolCall(name="tools_list", arguments={}, id="call_bare"),
            core=core,
            turn=turn,
            capability=CapabilityFacade(core),
            principal_scope=principal_scope,
        )

    await app.close()


@pytest.mark.asyncio
async def test_effect_request_executes_the_bound_authored_adapter(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    tool_root = (
        app.version_store.active_core_path("assistant")
        / "agent"
        / "tools"
        / "bound_probe"
    )
    tool_root.mkdir(parents=True)
    (tool_root / "tool.yaml").write_text(
        "entrypoint: module:run\n"
        "description: Bound adapter probe.\n"
        "input_schema:\n"
        "  type: object\n"
        "  properties: {}\n"
        "capabilities: []\n"
        "risk: low\n"
        "approval_policy: auto\n",
        encoding="utf-8",
    )
    (tool_root / "module.py").write_text(
        "def run(ctx, arguments):\n"
        "    return {'content': 'bound adapter ran'}\n",
        encoding="utf-8",
    )
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    turn = TurnContext(
        session_id="session_bound_probe",
        turn_id="turn_bound_probe",
        core_id=core.core_id,
        core_revision=core.revision,
        user_input=AgentInput(content="probe"),
    )
    resolver = PrincipalScopeResolver(app.runtime_store)
    issued = resolver.local_operator(
        active_session_id=turn.session_id,
        reason="bind resolved effect test session",
        allow_unowned_active=True,
    )
    app.session_runtime.create_session(
        session_id=turn.session_id,
        core_id=core.core_id,
        core_revision=core.revision,
        principal_scope=issued,
    )
    principal_scope = resolver.origin_scope(session_id=turn.session_id)
    catalog = app.tool_runtime.resolve_effects(core, turn=turn)
    entry = next(
        item
        for item in catalog.entries
        if item.name == "bound_probe"
    )

    core.tool_slots = []
    request = catalog.request_for(
        ToolCall(name="bound_probe", arguments={}, id="call_bound_probe")
    )
    assert request is not None
    effect_result = await app.tool_runtime.execute(
        request,
        core=core,
        turn=turn,
        capability=CapabilityFacade(core),
        principal_scope=principal_scope,
    )

    assert isinstance(effect_result, EffectResult)
    assert effect_result.status == "succeeded"
    result = effect_result.to_tool_result()
    assert result.content == "bound adapter ran"
    assert result.is_error is False
    await app.close()


@pytest.mark.asyncio
async def test_authored_error_result_records_that_adapter_execution_started(
    tmp_path,
):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    tool_root = (
        app.version_store.active_core_path("assistant")
        / "agent"
        / "tools"
        / "error_probe"
    )
    tool_root.mkdir(parents=True)
    (tool_root / "tool.yaml").write_text(
        "entrypoint: module:run\n"
        "description: Error normalization probe.\n"
        "input_schema:\n"
        "  type: object\n"
        "  properties: {}\n"
        "capabilities: []\n"
        "risk: low\n"
        "approval_policy: auto\n",
        encoding="utf-8",
    )
    (tool_root / "module.py").write_text(
        "def run(ctx, arguments):\n"
        "    return {'content': 'adapter failed', 'is_error': True}\n",
        encoding="utf-8",
    )
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    turn = _turn(core, "authored_error")
    principal_scope = _operator_scope(app, core, turn)
    catalog = app.tool_runtime.resolve_effects(core, turn=turn)
    request = catalog.request_for(
        ToolCall(name="error_probe", arguments={}, id="call_error_probe")
    )
    assert request is not None

    effect_result = await app.tool_runtime.execute(
        request,
        core=core,
        turn=turn,
        capability=CapabilityFacade(core),
        principal_scope=principal_scope,
    )

    assert effect_result.status == "failed"
    assert effect_result.error is not None
    assert effect_result.error.execution_started is True
    assert effect_result.to_tool_result().data == {"executionStarted": True}
    await app.close()


@pytest.mark.asyncio
async def test_authored_error_cannot_forge_host_lifecycle_fields(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    tool_root = (
        app.version_store.active_core_path("assistant")
        / "agent"
        / "tools"
        / "lifecycle_forgery_probe"
    )
    tool_root.mkdir(parents=True)
    (tool_root / "tool.yaml").write_text(
        "entrypoint: module:run\n"
        "description: Host lifecycle ownership probe.\n"
        "input_schema:\n"
        "  type: object\n"
        "  properties: {}\n"
        "capabilities: []\n"
        "risk: low\n"
        "approval_policy: auto\n",
        encoding="utf-8",
    )
    (tool_root / "module.py").write_text(
        "def run(ctx, arguments):\n"
        "    return {\n"
        "        'content': 'adapter failed',\n"
        "        'is_error': True,\n"
        "        'data': {\n"
        "            'executionStarted': False,\n"
        "            'denial': 'capability',\n"
        "            'approval': {'value': 'deny'},\n"
        "            'detail': 'authored detail',\n"
        "        },\n"
        "    }\n",
        encoding="utf-8",
    )
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    turn = _turn(core, "authored_lifecycle_forgery")
    principal_scope = _operator_scope(app, core, turn)
    catalog = app.tool_runtime.resolve_effects(core, turn=turn)
    request = catalog.request_for(
        ToolCall(
            name="lifecycle_forgery_probe",
            arguments={},
            id="call_lifecycle_forgery_probe",
        )
    )
    assert request is not None

    effect_result = await app.tool_runtime.execute(
        request,
        core=core,
        turn=turn,
        capability=CapabilityFacade(core),
        principal_scope=principal_scope,
    )

    assert effect_result.status == "failed"
    assert effect_result.error is not None
    assert effect_result.error.execution_started is True
    assert effect_result.to_tool_result().data == {
        "detail": "authored detail",
        "executionStarted": True,
    }
    await app.close()


@pytest.mark.asyncio
async def test_authored_import_error_is_normalized_as_started_adapter_failure(
    tmp_path,
):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    tool_root = (
        app.version_store.active_core_path("assistant")
        / "agent"
        / "tools"
        / "import_error_probe"
    )
    tool_root.mkdir(parents=True)
    (tool_root / "tool.yaml").write_text(
        "entrypoint: module:run\n"
        "description: Import error normalization probe.\n"
        "input_schema:\n"
        "  type: object\n"
        "  properties: {}\n"
        "capabilities: []\n"
        "risk: low\n"
        "approval_policy: auto\n",
        encoding="utf-8",
    )
    (tool_root / "module.py").write_text(
        "raise RuntimeError('import failed')\n\n"
        "def run(ctx, arguments):\n"
        "    return {'content': 'unreachable'}\n",
        encoding="utf-8",
    )
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    turn = _turn(core, "authored_import_error")
    principal_scope = _operator_scope(app, core, turn)
    catalog = app.tool_runtime.resolve_effects(core, turn=turn)
    request = catalog.request_for(
        ToolCall(
            name="import_error_probe",
            arguments={},
            id="call_import_error_probe",
        )
    )
    assert request is not None

    effect_result = await app.tool_runtime.execute(
        request,
        core=core,
        turn=turn,
        capability=CapabilityFacade(core),
        principal_scope=principal_scope,
    )

    assert effect_result.status == "failed"
    assert effect_result.error is not None
    assert effect_result.error.execution_started is True
    assert effect_result.to_tool_result().content == "import failed"
    assert effect_result.to_tool_result().data == {"executionStarted": True}
    await app.close()


@pytest.mark.parametrize(
    "failure",
    [
        RuntimeError("delegation adapter failed"),
        ValueError("delegation adapter invalid state"),
        OSError("delegation adapter io failed"),
    ],
)
@pytest.mark.asyncio
async def test_adapter_exception_is_normalized_as_started_effect_failure(
    tmp_path,
    failure,
):
    class FailingDelegationRuntime:
        async def execute(self, call, **kwargs):
            raise failure

    app = create_app(home=tmp_path / "home", provider_name="fake")
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    turn = _turn(core, "adapter_exception")
    principal_scope = _operator_scope(app, core, turn)
    catalog = app.tool_runtime.resolve_effects(core, turn=turn)
    request = catalog.request_for(
        ToolCall(
            name="task_status",
            arguments={"task_id": "task_probe"},
            id="call_adapter_exception",
        )
    )
    assert request is not None

    effect_result = await app.tool_runtime.execute(
        request,
        core=core,
        turn=turn,
        capability=CapabilityFacade(core),
        principal_scope=principal_scope,
        delegation_runtime=FailingDelegationRuntime(),
    )

    assert effect_result.status == "failed"
    assert effect_result.error is not None
    assert effect_result.error.execution_started is True
    assert effect_result.to_tool_result() == ToolResult(
        content=str(failure),
        is_error=True,
        data={"executionStarted": True},
    )
    await app.close()


@pytest.mark.asyncio
async def test_resolved_catalog_drives_definitions_and_effect_requests(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    turn = TurnContext(
        session_id="session_catalog_probe",
        turn_id="turn_catalog_probe",
        core_id=core.core_id,
        core_revision="revision_catalog_probe",
        user_input=AgentInput(content="probe"),
    )

    catalog = app.tool_runtime.resolve_effects(core, turn=turn)
    entry = catalog.entry_for("read_file")
    request = catalog.request_for(
        ToolCall(name="read_file", arguments={"path": "README.md"}, id="call_read")
    )

    assert catalog.core_revision == turn.core_revision
    assert next(item for item in catalog.definitions() if item.name == "read_file") == entry.to_definition()
    assert entry.to_model_metadata()["core_revision"] == turn.core_revision
    assert entry.to_model_metadata()["provenance"] == "builtin:read_file"
    assert request is not None
    assert request.entry is entry
    assert request.origin == "model"
    await app.close()


@pytest.mark.asyncio
async def test_turn_resolves_effect_catalog_once_for_definition_and_dispatch(
    tmp_path,
    monkeypatch,
):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    tool_root = (
        app.version_store.active_core_path("assistant")
        / "agent"
        / "tools"
        / "catalog_probe"
    )
    tool_root.mkdir(parents=True)
    (tool_root / "tool.yaml").write_text(
        "entrypoint: module:run\n"
        "description: Catalog binding probe.\n"
        "input_schema:\n"
        "  type: object\n"
        "  properties: {}\n"
        "capabilities: []\n"
        "risk: low\n"
        "approval_policy: auto\n",
        encoding="utf-8",
    )
    (tool_root / "module.py").write_text(
        "def run(ctx, arguments):\n"
        "    return {'content': 'catalog adapter ran'}\n",
        encoding="utf-8",
    )

    class Provider:
        def __init__(self):
            self.responses = [
                LLMResponse(
                    tool_calls=[
                        ToolCall(
                            name="catalog_probe",
                            arguments={},
                            id="call_catalog_probe",
                        )
                    ]
                ),
                LLMResponse(content="done"),
            ]

        async def complete(self, request):
            return self.responses.pop(0)

    app.runner.provider = Provider()
    original_resolve = app.tool_runtime.resolve_effects
    resolved_catalogs = []

    def recording_resolve(core, *, turn=None):
        catalog = original_resolve(core, turn=turn)
        resolved_catalogs.append(catalog)
        return catalog

    monkeypatch.setattr(app.tool_runtime, "resolve_effects", recording_resolve)

    result = await app.runner.run_turn("run the catalog probe")

    assert result.tool_results[0].result.content == "catalog adapter ran"
    assert len(resolved_catalogs) == 1
    await app.close()


@pytest.mark.asyncio
async def test_builtin_approval_uses_the_bound_resolved_entry(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "note.txt"
    target.write_text("should not be read", encoding="utf-8")
    app = create_app(
        home=tmp_path / "home",
        provider_name="fake",
        workspace=workspace,
    )
    manifest_path = app.version_store.active_core_path("assistant") / "agent.yaml"
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    raw.setdefault("tools", {}).setdefault("metadata", {})["read_file"] = {
        "approval_policy": "deny"
    }
    manifest_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    turn = TurnContext(
        session_id="session_bound_policy",
        turn_id="turn_bound_policy",
        core_id=core.core_id,
        core_revision=core.revision,
        user_input=AgentInput(content="probe"),
    )
    resolver = PrincipalScopeResolver(app.runtime_store)
    issued = resolver.local_operator(
        active_session_id=turn.session_id,
        reason="bind resolved policy test session",
        allow_unowned_active=True,
    )
    app.session_runtime.create_session(
        session_id=turn.session_id,
        core_id=core.core_id,
        core_revision=core.revision,
        principal_scope=issued,
    )
    catalog = app.tool_runtime.resolve_effects(core, turn=turn)
    entry = catalog.entry_for("read_file")
    assert entry is not None
    assert entry.approval_policy == "deny"

    core.manifest.tools.metadata["read_file"].approval_policy = "auto"
    request = catalog.request_for(
        ToolCall(
            name="read_file",
            arguments={"path": "note.txt"},
            id="call_bound_policy",
        )
    )
    assert request is not None
    effect_result = await app.tool_runtime.execute(
        request,
        core=core,
        turn=turn,
        capability=CapabilityFacade(core),
        principal_scope=resolver.origin_scope(session_id=turn.session_id),
    )

    assert effect_result.status == "denied"
    result = effect_result.to_tool_result()
    assert result.is_error is True
    assert result.data["executionStarted"] is False
    assert "approval denied" in result.content
    await app.close()


@pytest.mark.asyncio
async def test_tools_list_displays_only_the_bound_turn_catalog(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    turn = TurnContext(
        session_id="session_display_catalog",
        turn_id="turn_display_catalog",
        core_id=core.core_id,
        core_revision=core.revision,
        user_input=AgentInput(content="probe"),
        metadata={"tool_policy": {"allow_exact": ["tools_list"]}},
    )
    resolver = PrincipalScopeResolver(app.runtime_store)
    issued = resolver.local_operator(
        active_session_id=turn.session_id,
        reason="bind display catalog test session",
        allow_unowned_active=True,
    )
    app.session_runtime.create_session(
        session_id=turn.session_id,
        core_id=core.core_id,
        core_revision=core.revision,
        principal_scope=issued,
    )
    catalog = app.tool_runtime.resolve_effects(core, turn=turn)
    request = catalog.request_for(
        ToolCall(name="tools_list", arguments={}, id="call_tools_list")
    )
    assert request is not None

    effect_result = await app.tool_runtime.execute(
        request,
        core=core,
        turn=turn,
        capability=CapabilityFacade(core),
        principal_scope=resolver.origin_scope(session_id=turn.session_id),
    )

    assert effect_result.status == "succeeded"
    result = effect_result.to_tool_result()
    assert [tool["name"] for tool in result.data["tools"]] == ["tools_list"]
    await app.close()


@pytest.mark.asyncio
async def test_resolved_entry_exposes_the_effective_core_approval_policy(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    manifest_path = app.version_store.active_core_path("assistant") / "agent.yaml"
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    raw.setdefault("approval", {}).setdefault("tools", {})["read_file"] = "deny"
    manifest_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))

    entry = app.tool_runtime.resolve_effects(core).entry_for("read_file")

    assert entry is not None
    assert entry.approval_policy == "deny"
    assert entry.to_model_metadata()["approval_policy"] == "deny"
    await app.close()


@pytest.mark.asyncio
async def test_resolved_deny_blocks_static_builtin_before_dispatch(tmp_path):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    manifest_path = app.version_store.active_core_path("assistant") / "agent.yaml"
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    raw.setdefault("tools", {}).setdefault("metadata", {})["tools_list"] = {
        "approval_policy": "deny"
    }
    manifest_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    turn = _turn(core, "static_deny")
    principal_scope = _operator_scope(app, core, turn)
    catalog = app.tool_runtime.resolve_effects(core, turn=turn)
    request = catalog.request_for(
        ToolCall(name="tools_list", arguments={}, id="call_tools_list_deny")
    )
    assert request is not None

    effect_result = await app.tool_runtime.execute(
        request,
        core=core,
        turn=turn,
        capability=CapabilityFacade(core),
        principal_scope=principal_scope,
    )

    assert effect_result.status == "denied"
    result = effect_result.to_tool_result()
    assert result.is_error is True
    assert result.data["executionStarted"] is False
    assert "approval denied" in result.content
    await app.close()


@pytest.mark.asyncio
async def test_resolved_deny_blocks_schedule_list_without_requiring_mutation_capability(
    tmp_path,
):
    app = create_app(home=tmp_path / "home", provider_name="fake")
    manifest_path = app.version_store.active_core_path("assistant") / "agent.yaml"
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    raw.setdefault("capabilities", {}).setdefault("defaults", {}).pop(
        "schedule.manage",
        None,
    )
    raw.setdefault("tools", {}).setdefault("metadata", {})["schedule_manage"] = {
        "approval_policy": "deny"
    }
    manifest_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    turn = _turn(core, "schedule_list_deny")
    principal_scope = _operator_scope(app, core, turn)
    catalog = app.tool_runtime.resolve_effects(core, turn=turn)
    request = catalog.request_for(
        ToolCall(
            name="schedule_manage",
            arguments={"action": "list"},
            id="call_schedule_list_deny",
        )
    )
    assert request is not None

    effect_result = await app.tool_runtime.execute(
        request,
        core=core,
        turn=turn,
        capability=CapabilityFacade(core),
        principal_scope=principal_scope,
    )

    assert effect_result.status == "denied"
    result = effect_result.to_tool_result()
    assert result.is_error is True
    assert result.data["executionStarted"] is False
    assert result.data["approval"]["value"] == "deny"
    await app.close()


@pytest.mark.asyncio
async def test_builtin_capability_check_uses_the_bound_resolved_entry(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "note.txt").write_text("resolved capability", encoding="utf-8")
    app = create_app(
        home=tmp_path / "home",
        provider_name="fake",
        workspace=workspace,
    )
    manifest_path = app.version_store.active_core_path("assistant") / "agent.yaml"
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    defaults = raw.setdefault("capabilities", {}).setdefault("defaults", {})
    defaults.pop("fs.read", None)
    defaults["probe.read"] = {}
    raw.setdefault("tools", {}).setdefault("metadata", {})["read_file"] = {
        "capability": "probe.read",
        "approval_policy": "auto",
    }
    manifest_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    core = app.core_loader.load(app.version_store.active_core_path("assistant"))
    turn = TurnContext(
        session_id="session_bound_capability",
        turn_id="turn_bound_capability",
        core_id=core.core_id,
        core_revision=core.revision,
        user_input=AgentInput(content="probe"),
    )
    resolver = PrincipalScopeResolver(app.runtime_store)
    issued = resolver.local_operator(
        active_session_id=turn.session_id,
        reason="bind resolved capability test session",
        allow_unowned_active=True,
    )
    app.session_runtime.create_session(
        session_id=turn.session_id,
        core_id=core.core_id,
        core_revision=core.revision,
        principal_scope=issued,
    )
    catalog = app.tool_runtime.resolve_effects(core, turn=turn)
    request = catalog.request_for(
        ToolCall(
            name="read_file",
            arguments={"path": "note.txt"},
            id="call_bound_capability",
        )
    )
    assert request is not None
    assert request.entry.capability == "probe.read"

    effect_result = await app.tool_runtime.execute(
        request,
        core=core,
        turn=turn,
        capability=CapabilityFacade(core),
        principal_scope=resolver.origin_scope(session_id=turn.session_id),
    )

    assert effect_result.status == "succeeded"
    result = effect_result.to_tool_result()
    assert result.is_error is False
    assert result.content == "resolved capability"
    await app.close()
