import shutil

import pytest
import yaml

from demiurge.app import source_agents_root
from demiurge.core import CoreLoadError, CoreLoader, load_slot_callable


def _pipelines_yaml_path(core_root):
    return core_root / "agent" / "pipelines.yaml"


def _read_pipelines_yaml(core_root):
    return yaml.safe_load(_pipelines_yaml_path(core_root).read_text(encoding="utf-8"))


def _write_pipelines_yaml(core_root, data):
    _pipelines_yaml_path(core_root).write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _declare_slot(core_root, phase, slot_id, **values):
    slot_dir = core_root / "agent" / phase / slot_id
    slot_dir.mkdir(parents=True, exist_ok=True)
    declaration = {"failure_policy": "soft", "capabilities": []}
    declaration.update(values)
    (slot_dir / "slot.yaml").write_text(yaml.safe_dump(declaration, sort_keys=False), encoding="utf-8")


def _set_pipeline(core_root, phase, *, serial=None, parallel=None, extra=None):
    data = _read_pipelines_yaml(core_root)
    pipeline = {"serial": list(serial or [])}
    if phase != "bootstrap":
        pipeline["parallel"] = list(parallel or [])
    if extra:
        pipeline.update(extra)
    data[phase] = pipeline
    _write_pipelines_yaml(core_root, data)


def test_loader_discovers_source_agent_slots():
    core = CoreLoader().load(source_agents_root() / "assistant")

    assert core.core_id == "assistant"
    assert core.version == "0001"
    assert core.manifest.model.provider == "auto"
    assert core.manifest.model.model_name is None
    assert core.manifest.model.model_options == {}
    assert core.manifest.runtime.max_model_steps == 90
    assert core.manifest.runtime.workspace is None
    assert sorted(core.manifest.channels) == ["email", "matrix", "mattermost", "slack", "telegram", "webhook"]
    assert all(channel.enabled is False for channel in core.manifest.channels.values())
    assert core.manifest.channels["telegram"].bot_token_env == "DEMIURGE_TELEGRAM_BOT_TOKEN"
    assert core.manifest.channels["telegram"].message_format == "markdown_v2"
    assert core.manifest.channels["telegram"].register_commands is True
    assert core.manifest.channels["telegram"].send_typing is True
    assert core.manifest.channels["telegram"].rich_messages is True
    assert core.manifest.channels["telegram"].reply_to_mode == "off"
    assert core.manifest.channels["telegram"].allowed_users == []
    assert core.manifest.channels["telegram"].allowed_chats == []
    assert core.manifest.channels["telegram"].unauthorized_response == "brief"
    assert core.manifest.channels["webhook"].token_env == "DEMIURGE_WEBHOOK_TOKEN"
    assert core.manifest.channels["slack"].signing_secret_env == "SLACK_SIGNING_SECRET"
    assert core.manifest.channels["mattermost"].webhook_token_env == "MATTERMOST_WEBHOOK_TOKEN"
    assert core.manifest.channels["matrix"].access_token_env == "MATRIX_ACCESS_TOKEN"
    assert core.manifest.channels["email"].smtp_username_env == "DEMIURGE_SMTP_USERNAME"
    assert core.manifest.tools.toolsets == ["coding", "demiurge_control", "schedule"]
    assert [slot.slot_id for slot in core.input_slots] == ["base_input"]
    assert [slot.slot_id for slot in core.input_pipeline.serial] == ["base_input"]
    assert [slot.slot_id for slot in core.input_pipeline.parallel] == []
    assert [slot.slot_id for slot in core.output_slots] == ["base_output"]
    assert [slot.slot_id for slot in core.output_pipeline.serial] == ["base_output"]
    assert [slot.slot_id for slot in core.output_pipeline.parallel] == []
    assert [slot.slot_id for slot in core.tool_slots] == ["echo"]
    assert core.skills == []
    assert core.schedules == []
    assert core.mcp_servers == []
    assert core.manifest.slots["soul"] == "agent/SOUL.md"
    assert core.manifest.slots["mcp"] == "agent/mcp"
    assert "demiurge assistant" in core.soul


def test_loader_discovers_evolver_source_agent():
    core = CoreLoader().load(source_agents_root() / "evolver")

    assert core.core_id == "evolver"
    assert core.version == "0001"
    assert core.manifest.runtime.max_model_steps == 90
    assert core.manifest.runtime.workspace is None
    assert core.manifest.model.model_name is None
    assert core.manifest.model.model_options == {}
    assert [slot.slot_id for slot in core.input_slots] == ["base_input"]
    assert [slot.slot_id for slot in core.input_pipeline.serial] == ["base_input"]
    assert [slot.slot_id for slot in core.output_slots] == ["base_output"]
    assert [slot.slot_id for slot in core.output_pipeline.serial] == ["base_output"]
    assert core.bootstrap_enabled is False
    assert core.bootstrap_slots == []
    assert core.bootstrap_pipeline.serial == []
    assert core.schedules == []
    assert core.manifest.slots["soul"] == "agent/SOUL.md"
    assert "demiurge evolver" in core.soul


def test_loader_accepts_enabled_telegram_channel(tmp_path):
    source = source_agents_root() / "assistant"
    target = tmp_path / "assistant"
    shutil.copytree(source, target)
    manifest_path = target / "agent.yaml"
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    raw["channels"]["telegram"]["enabled"] = True
    manifest_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    core = CoreLoader().load(target)

    assert core.manifest.channels["telegram"].enabled is True


def test_loader_accepts_core_runtime_workspace(tmp_path):
    source = source_agents_root() / "assistant"
    target = tmp_path / "assistant"
    shutil.copytree(source, target)
    manifest_path = target / "agent.yaml"
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    raw["runtime"]["workspace"] = "project"
    manifest_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    core = CoreLoader().load(target)

    assert core.manifest.runtime.workspace == "project"


@pytest.mark.parametrize("workspace", ["", "   ", 123])
def test_loader_rejects_invalid_core_runtime_workspace(tmp_path, workspace):
    source = source_agents_root() / "assistant"
    target = tmp_path / "assistant"
    shutil.copytree(source, target)
    manifest_path = target / "agent.yaml"
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    raw["runtime"]["workspace"] = workspace
    manifest_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    with pytest.raises(CoreLoadError, match="runtime.workspace"):
        CoreLoader().load(target)


def test_loader_discovers_schedule_defaults_and_path_id(tmp_path):
    source = source_agents_root() / "assistant"
    target = tmp_path / "assistant"
    shutil.copytree(source, target)
    schedule_dir = target / "agent" / "schedules"
    schedule_dir.mkdir()
    (schedule_dir / "daily.yaml").write_text(
        'schedule: "0 9 * * *"\n'
        'prompt: "Daily report"\n',
        encoding="utf-8",
    )

    core = CoreLoader().load(target)

    assert [schedule.schedule_id for schedule in core.schedules] == ["daily"]
    schedule = core.schedules[0]
    assert schedule.enabled is True
    assert schedule.relative_path == "agent/schedules/daily.yaml"
    assert schedule.modules.input == ["base_input"]
    assert schedule.modules.output == ["base_output"]
    assert schedule.delivery.mode == "local"


def test_loader_rejects_schedule_timezone_field(tmp_path):
    source = source_agents_root() / "assistant"
    target = tmp_path / "assistant"
    shutil.copytree(source, target)
    schedule_dir = target / "agent" / "schedules"
    schedule_dir.mkdir()
    (schedule_dir / "daily.yaml").write_text(
        'schedule: "0 9 * * *"\n'
        "timezone: UTC\n"
        'prompt: "Daily report"\n',
        encoding="utf-8",
    )

    with pytest.raises(CoreLoadError, match="timezone"):
        CoreLoader().load(target)


def test_loader_schedule_root_can_be_overridden_by_slots(tmp_path):
    source = source_agents_root() / "assistant"
    target = tmp_path / "assistant"
    shutil.copytree(source, target)
    manifest_path = target / "agent.yaml"
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    raw.setdefault("slots", {})["schedules"] = "custom/schedules"
    manifest_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    schedule_dir = target / "custom" / "schedules"
    schedule_dir.mkdir(parents=True)
    (schedule_dir / "hourly.yaml").write_text(
        'schedule: "0 * * * *"\n'
        'prompt: "Hourly report"\n',
        encoding="utf-8",
    )

    core = CoreLoader().load(target)

    assert [schedule.relative_path for schedule in core.schedules] == ["custom/schedules/hourly.yaml"]


def test_loader_discovers_mcp_server_defaults_and_filters(tmp_path):
    source = source_agents_root() / "assistant"
    target = tmp_path / "assistant"
    shutil.copytree(source, target)
    mcp_dir = target / "agent" / "mcp"
    mcp_dir.mkdir()
    (mcp_dir / "docs.yaml").write_text(
        "transport: stdio\n"
        "command: node\n"
        "args:\n"
        "  - server.js\n"
        "env:\n"
        "  API_TOKEN: ${DOCS_TOKEN}\n"
        "tools:\n"
        "  include:\n"
        "    - search_docs\n",
        encoding="utf-8",
    )

    core = CoreLoader().load(target)

    assert [server.server_id for server in core.mcp_servers] == ["docs"]
    server = core.mcp_servers[0]
    assert server.relative_path == "agent/mcp/docs.yaml"
    assert server.manifest.transport == "stdio"
    assert server.manifest.command == "node"
    assert server.manifest.args == ["server.js"]
    assert server.manifest.env == {"API_TOKEN": "${DOCS_TOKEN}"}
    assert server.manifest.tools.include == ["search_docs"]
    assert server.manifest.risk == "medium"
    assert server.manifest.approval_policy == "prompt"
    assert server.capability == "mcp.call:docs"


def test_loader_discovers_streamable_http_mcp_server(tmp_path):
    source = source_agents_root() / "assistant"
    target = tmp_path / "assistant"
    shutil.copytree(source, target)
    mcp_dir = target / "agent" / "mcp"
    mcp_dir.mkdir()
    (mcp_dir / "remote.yaml").write_text(
        "transport: streamable_http\n"
        "url: https://example.test/mcp\n"
        "headers:\n"
        "  Authorization: Bearer ${REMOTE_TOKEN}\n"
        "approval_policy: auto\n",
        encoding="utf-8",
    )

    core = CoreLoader().load(target)

    server = core.mcp_servers[0]
    assert server.server_id == "remote"
    assert server.manifest.transport == "streamable_http"
    assert server.manifest.url == "https://example.test/mcp"
    assert server.manifest.headers["Authorization"] == "Bearer ${REMOTE_TOKEN}"
    assert server.manifest.approval_policy == "auto"


def test_loader_rejects_invalid_mcp_server(tmp_path):
    source = source_agents_root() / "assistant"
    target = tmp_path / "assistant"
    shutil.copytree(source, target)
    mcp_dir = target / "agent" / "mcp"
    mcp_dir.mkdir()
    (mcp_dir / "bad.yaml").write_text("transport: stdio\n", encoding="utf-8")

    with pytest.raises(CoreLoadError, match="invalid MCP server"):
        CoreLoader().load(target)


def test_loader_keeps_disabled_schedules(tmp_path):
    source = source_agents_root() / "assistant"
    target = tmp_path / "assistant"
    shutil.copytree(source, target)
    schedule_dir = target / "agent" / "schedules"
    schedule_dir.mkdir()
    (schedule_dir / "paused.yaml").write_text(
        "enabled: false\n"
        'schedule: "*/5 * * * *"\n'
        'prompt: "Paused report"\n',
        encoding="utf-8",
    )

    core = CoreLoader().load(target)

    assert len(core.schedules) == 1
    assert core.schedules[0].enabled is False


def test_loader_rejects_invalid_schedule_syntax(tmp_path):
    source = source_agents_root() / "assistant"
    target = tmp_path / "assistant"
    shutil.copytree(source, target)
    schedule_dir = target / "agent" / "schedules"
    schedule_dir.mkdir()
    (schedule_dir / "bad.yaml").write_text(
        'schedule: "not cron"\n'
        'prompt: "Bad schedule"\n',
        encoding="utf-8",
    )

    with pytest.raises(CoreLoadError, match="invalid schedule"):
        CoreLoader().load(target)


def test_loader_rejects_unknown_schedule_module(tmp_path):
    source = source_agents_root() / "assistant"
    target = tmp_path / "assistant"
    shutil.copytree(source, target)
    schedule_dir = target / "agent" / "schedules"
    schedule_dir.mkdir()
    (schedule_dir / "bad_module.yaml").write_text(
        'schedule: "0 9 * * *"\n'
        'prompt: "Bad module"\n'
        "modules:\n"
        "  input:\n"
        "    - missing_input\n"
        "  output:\n"
        "    - base_output\n",
        encoding="utf-8",
    )

    with pytest.raises(CoreLoadError, match="unknown input schedule module missing_input"):
        CoreLoader().load(target)


def test_loader_validates_telegram_schedule_target_allowlist(tmp_path):
    source = source_agents_root() / "assistant"
    target = tmp_path / "assistant"
    shutil.copytree(source, target)
    manifest_path = target / "agent.yaml"
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    raw["channels"]["telegram"]["allowed_users"] = [123]
    manifest_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    schedule_dir = target / "agent" / "schedules"
    schedule_dir.mkdir()
    (schedule_dir / "telegram.yaml").write_text(
        'schedule: "0 9 * * *"\n'
        'prompt: "Send report"\n'
        "delivery:\n"
        "  mode: telegram\n"
        "  chat_id: 123\n",
        encoding="utf-8",
    )

    core = CoreLoader().load(target)
    assert core.schedules[0].delivery.chat_id == 123

    (schedule_dir / "telegram.yaml").write_text(
        'schedule: "0 9 * * *"\n'
        'prompt: "Send report"\n'
        "delivery:\n"
        "  mode: telegram\n"
        "  chat_id: 456\n",
        encoding="utf-8",
    )
    with pytest.raises(CoreLoadError, match="not allowed"):
        CoreLoader().load(target)


def test_loader_accepts_configured_max_model_steps(tmp_path):
    source = source_agents_root() / "assistant"
    target = tmp_path / "assistant"
    shutil.copytree(source, target)
    manifest_path = target / "agent.yaml"
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    raw["runtime"]["max_model_steps"] = 12
    manifest_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    core = CoreLoader().load(target)

    assert core.manifest.runtime.max_model_steps == 12


def test_loader_rejects_max_model_steps_above_host_cap(tmp_path):
    source = source_agents_root() / "assistant"
    target = tmp_path / "assistant"
    shutil.copytree(source, target)
    manifest_path = target / "agent.yaml"
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    raw["runtime"]["max_model_steps"] = 91
    manifest_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    with pytest.raises(CoreLoadError, match="max_model_steps"):
        CoreLoader().load(target)


def test_loader_rejects_missing_manifest(tmp_path):
    try:
        CoreLoader().load(tmp_path)
    except CoreLoadError as exc:
        assert "missing agent.yaml" in str(exc)
    else:
        raise AssertionError("expected CoreLoadError")


def test_loader_rejects_duplicate_skill_ids(tmp_path):
    source = source_agents_root() / "assistant"
    target = tmp_path / "assistant"
    shutil.copytree(source, target)
    _write_packaged_debugging_skill(target)
    (target / "agent" / "skills" / "debugging.md").write_text(
        "---\ndescription: duplicate\n---\n\n# duplicate\n",
        encoding="utf-8",
    )

    try:
        CoreLoader().load(target)
    except CoreLoadError as exc:
        assert "duplicate skill id debugging" in str(exc)
    else:
        raise AssertionError("expected CoreLoadError")


def test_loader_requires_pipelines_yaml(tmp_path):
    source = source_agents_root() / "assistant"
    target = tmp_path / "assistant"
    shutil.copytree(source, target)
    _pipelines_yaml_path(target).unlink()

    with pytest.raises(CoreLoadError, match="missing pipelines.yaml"):
        CoreLoader().load(target)


def test_loader_discovers_optional_bootstrap_serial_pipeline(tmp_path):
    source = source_agents_root() / "assistant"
    target = tmp_path / "assistant"
    shutil.copytree(source, target)

    core = CoreLoader().load(target)

    assert core.bootstrap_enabled is True
    assert [slot.slot_id for slot in core.bootstrap_slots] == ["session_context"]
    assert [slot.slot_id for slot in core.bootstrap_pipeline.serial] == ["session_context"]
    assert core.bootstrap_pipeline.parallel == []


def test_loader_rejects_bootstrap_parallel_key(tmp_path):
    source = source_agents_root() / "assistant"
    target = tmp_path / "assistant"
    shutil.copytree(source, target)
    _set_pipeline(target, "bootstrap", serial=[], extra={"parallel": []})

    with pytest.raises(CoreLoadError, match="invalid pipelines.yaml bootstrap key"):
        CoreLoader().load(target)


def test_loader_rejects_unknown_bootstrap_pipeline_slot(tmp_path):
    source = source_agents_root() / "assistant"
    target = tmp_path / "assistant"
    shutil.copytree(source, target)
    _set_pipeline(target, "bootstrap", serial=["missing"])

    with pytest.raises(CoreLoadError, match="unknown bootstrap pipeline slot: missing"):
        CoreLoader().load(target)


def test_loader_rejects_duplicate_bootstrap_pipeline_slot(tmp_path):
    source = source_agents_root() / "assistant"
    target = tmp_path / "assistant"
    shutil.copytree(source, target)
    _set_pipeline(target, "bootstrap", serial=["session_context", "session_context"])

    with pytest.raises(CoreLoadError, match="duplicate bootstrap pipeline slot session_context"):
        CoreLoader().load(target)


def test_loader_rejects_invalid_bootstrap_failure_policy(tmp_path):
    source = source_agents_root() / "assistant"
    target = tmp_path / "assistant"
    shutil.copytree(source, target)
    _declare_slot(target, "bootstrap", "bad_policy", failure_policy="explode")
    _set_pipeline(target, "bootstrap", serial=["bad_policy"])

    with pytest.raises(CoreLoadError, match="invalid bootstrap module failure_policy"):
        CoreLoader().load(target)


def test_loader_rejects_unknown_pipeline_slot(tmp_path):
    source = source_agents_root() / "assistant"
    target = tmp_path / "assistant"
    shutil.copytree(source, target)
    _set_pipeline(target, "input", serial=["missing"], parallel=[])

    with pytest.raises(CoreLoadError, match="unknown input pipeline slot: missing"):
        CoreLoader().load(target)


def test_loader_rejects_duplicate_pipeline_slot(tmp_path):
    source = source_agents_root() / "assistant"
    target = tmp_path / "assistant"
    shutil.copytree(source, target)
    _set_pipeline(target, "output", serial=["base_output"], parallel=["base_output"])

    with pytest.raises(CoreLoadError, match="duplicate output pipeline slot base_output"):
        CoreLoader().load(target)


def test_loader_rejects_unknown_pipeline_keys(tmp_path):
    source = source_agents_root() / "assistant"
    target = tmp_path / "assistant"
    shutil.copytree(source, target)
    _set_pipeline(target, "input", serial=["base_input"], extra={"unexpected": ["profile"]})

    with pytest.raises(CoreLoadError, match="invalid pipelines.yaml input key"):
        CoreLoader().load(target)


def test_slot_modules_use_isolated_relative_imports(tmp_path):
    source = source_agents_root() / "assistant"
    target = tmp_path / "assistant"
    shutil.copytree(source, target)
    for slot_id, value in {"relative_a": "A", "relative_b": "B"}.items():
        slot = target / "agent" / "output" / slot_id
        slot.mkdir(parents=True)
        _declare_slot(target, "output", slot_id, description="relative import test", failure_policy="hard")
        (slot / "module.py").write_text(
            "from .helper import VALUE\n\n"
            "def process(ctx):\n"
            "    return VALUE\n",
            encoding="utf-8",
        )
        (slot / "helper.py").write_text(f"VALUE = {value!r}\n", encoding="utf-8")
    _set_pipeline(target, "output", serial=["base_output", "relative_a", "relative_b"], parallel=[])

    core = CoreLoader().load(target)
    slots = {slot.slot_id: slot for slot in core.output_slots}

    assert load_slot_callable(slots["relative_a"])(None) == "A"
    assert load_slot_callable(slots["relative_b"])(None) == "B"


def test_loader_rejects_duplicate_skill_names(tmp_path):
    source = source_agents_root() / "assistant"
    target = tmp_path / "assistant"
    shutil.copytree(source, target)
    skills = target / "agent" / "skills"
    skills.mkdir(parents=True, exist_ok=True)
    (skills / "first.md").write_text(
        "---\nname: debugging\ndescription: duplicate name\n---\n\n# first\n",
        encoding="utf-8",
    )
    (target / "agent" / "skills" / "other.md").write_text(
        "---\nname: debugging\ndescription: duplicate name\n---\n\n# duplicate\n",
        encoding="utf-8",
    )

    try:
        CoreLoader().load(target)
    except CoreLoadError as exc:
        assert "duplicate skill id debugging" in str(exc)
    else:
        raise AssertionError("expected CoreLoadError")


def test_loader_ignores_unlinked_and_symlink_skill_files(tmp_path):
    source = source_agents_root() / "assistant"
    target = tmp_path / "assistant"
    shutil.copytree(source, target)
    _write_packaged_debugging_skill(target)
    debugging = target / "agent" / "skills" / "debugging"
    (debugging / "notes").mkdir()
    (debugging / "notes" / "private.md").write_text("not linked", encoding="utf-8")
    (debugging / "references" / "link.md").symlink_to(tmp_path / "outside.md")

    core = CoreLoader().load(target)
    skill = core.skill_by_id("debugging")

    assert skill is not None
    assert skill.linked_files == {"references": ["references/checklist.md"]}


def _write_packaged_debugging_skill(core_root):
    skill = core_root / "agent" / "skills" / "debugging"
    references = skill / "references"
    references.mkdir(parents=True, exist_ok=True)
    (skill / "SKILL.md").write_text(
        "---\n"
        "name: debugging\n"
        "description: Debug failing commands.\n"
        "category: development\n"
        "---\n\n"
        "# Debugging\n\n"
        "Start from the exact failing command.\n",
        encoding="utf-8",
    )
    (references / "checklist.md").write_text("# Debugging Checklist\n", encoding="utf-8")


def test_loader_rejects_unknown_tool_metadata_fields(tmp_path):
    source = source_agents_root() / "assistant"
    target = tmp_path / "assistant"
    shutil.copytree(source, target)
    manifest_path = target / "agent.yaml"
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    raw.setdefault("tools", {}).setdefault("metadata", {})["read_file"] = {"unknown": True}
    manifest_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    with pytest.raises(CoreLoadError, match="invalid agent.yaml"):
        CoreLoader().load(target)


def test_loader_rejects_legacy_allow_builtin_tools_field(tmp_path):
    source = source_agents_root() / "assistant"
    target = tmp_path / "assistant"
    shutil.copytree(source, target)
    manifest_path = target / "agent.yaml"
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    raw["tools"] = {"allow_builtin": ["read_file"]}
    manifest_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    with pytest.raises(CoreLoadError, match="invalid agent.yaml"):
        CoreLoader().load(target)


def test_loader_rejects_unknown_toolset(tmp_path):
    source = source_agents_root() / "assistant"
    target = tmp_path / "assistant"
    shutil.copytree(source, target)
    manifest_path = target / "agent.yaml"
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    raw["tools"] = {"toolsets": ["missing"]}
    manifest_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    with pytest.raises(CoreLoadError, match="unknown toolset"):
        CoreLoader().load(target)


def test_loader_ignores_channel_slots_manifest_key(tmp_path):
    source = source_agents_root() / "assistant"
    target = tmp_path / "assistant"
    shutil.copytree(source, target)
    manifest_path = target / "agent.yaml"
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    raw.setdefault("slots", {})["channels"] = "agent/channels"
    manifest_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    core = CoreLoader().load(target)

    assert not hasattr(core, "channel_slots")


def test_loader_does_not_auto_scan_agent_channels_directory(tmp_path):
    source = source_agents_root() / "assistant"
    target = tmp_path / "assistant"
    shutil.copytree(source, target)
    channel_slot = target / "agent" / "channels" / "telegram"
    channel_slot.mkdir(parents=True, exist_ok=True)
    (channel_slot / "slot.yaml").write_text(
        "kind: channel\ntype: builtin\nname: telegram\nenabled: true\n",
        encoding="utf-8",
    )

    core = CoreLoader().load(target)

    assert not hasattr(core, "channel_slots")
