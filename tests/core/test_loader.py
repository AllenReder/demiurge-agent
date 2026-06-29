import shutil

import pytest
import yaml

from demiurge.app import source_agents_root
from demiurge.core import CoreLoadError, CoreLoader, load_slot_callable


def test_loader_discovers_source_agent_slots():
    core = CoreLoader().load(source_agents_root() / "assistant")

    assert core.core_id == "assistant"
    assert core.version == "0001"
    assert core.manifest.model.provider == "auto"
    assert core.manifest.model.model_name is None
    assert core.manifest.model.model_name_env == "DEMIURGE_MODEL_NAME"
    assert core.manifest.model.base_url is None
    assert core.manifest.model.base_url_env == "DEMIURGE_BASE_URL"
    assert core.manifest.model.api_key is None
    assert core.manifest.model.api_key_env == "DEMIURGE_API_KEY"
    assert core.manifest.runtime.max_model_steps == 90
    assert sorted(core.manifest.channels) == ["telegram"]
    assert core.manifest.channels["telegram"].enabled is False
    assert core.manifest.channels["telegram"].bot_token_env == "DEMIURGE_TELEGRAM_BOT_TOKEN"
    assert core.manifest.channels["telegram"].message_format == "markdown_v2"
    assert core.manifest.channels["telegram"].register_commands is True
    assert core.manifest.channels["telegram"].send_typing is True
    assert core.manifest.channels["telegram"].rich_messages is True
    assert core.manifest.channels["telegram"].reply_to_mode == "off"
    assert core.manifest.channels["telegram"].allowed_users == []
    assert core.manifest.channels["telegram"].allowed_chats == []
    assert core.manifest.channels["telegram"].unauthorized_response == "brief"
    assert core.manifest.tools.toolsets == ["coding", "demiurge_control"]
    assert [slot.slot_id for slot in core.input_slots] == ["base_input"]
    assert [slot.slot_id for slot in core.input_pipeline.serial] == ["base_input"]
    assert [slot.slot_id for slot in core.input_pipeline.parallel] == []
    assert [slot.slot_id for slot in core.output_slots] == ["base_output"]
    assert [slot.slot_id for slot in core.output_pipeline.serial] == ["base_output"]
    assert [slot.slot_id for slot in core.output_pipeline.parallel] == []
    assert [slot.slot_id for slot in core.tool_slots] == ["echo"]
    assert core.skills == []
    assert core.schedules == []
    assert core.manifest.slots["soul"] == "agent/SOUL.md"
    assert "demiurge assistant" in core.soul


def test_loader_discovers_evolver_source_agent():
    core = CoreLoader().load(source_agents_root() / "evolver")

    assert core.core_id == "evolver"
    assert core.version == "0001"
    assert core.manifest.runtime.max_model_steps == 90
    assert core.manifest.model.model_name is None
    assert core.manifest.model.model_name_env == "DEMIURGE_EVOLVER_MODEL_NAME"
    assert core.manifest.model.base_url is None
    assert core.manifest.model.base_url_env == "DEMIURGE_EVOLVER_BASE_URL"
    assert core.manifest.model.api_key is None
    assert core.manifest.model.api_key_env == "DEMIURGE_EVOLVER_API_KEY"
    assert [slot.slot_id for slot in core.input_slots] == ["base_input"]
    assert [slot.slot_id for slot in core.input_pipeline.serial] == ["base_input"]
    assert [slot.slot_id for slot in core.output_slots] == ["base_output"]
    assert [slot.slot_id for slot in core.output_pipeline.serial] == ["base_output"]
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
    assert schedule.timezone == "UTC"
    assert schedule.modules.input == ["base_input"]
    assert schedule.modules.output == ["base_output"]
    assert schedule.delivery.mode == "local"


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


def test_loader_requires_input_and_output_pipeline_files(tmp_path):
    source = source_agents_root() / "assistant"
    target = tmp_path / "assistant"
    shutil.copytree(source, target)
    (target / "agent" / "input" / "pipeline.yaml").unlink()

    with pytest.raises(CoreLoadError, match="missing input pipeline"):
        CoreLoader().load(target)

    shutil.copytree(source, target, dirs_exist_ok=True)
    (target / "agent" / "output" / "pipeline.yaml").unlink()

    with pytest.raises(CoreLoadError, match="missing output pipeline"):
        CoreLoader().load(target)


def test_loader_rejects_unknown_pipeline_slot(tmp_path):
    source = source_agents_root() / "assistant"
    target = tmp_path / "assistant"
    shutil.copytree(source, target)
    (target / "agent" / "input" / "pipeline.yaml").write_text(
        "serial:\n  - missing\nparallel: []\n",
        encoding="utf-8",
    )

    with pytest.raises(CoreLoadError, match="unknown input pipeline slot: missing"):
        CoreLoader().load(target)


def test_loader_rejects_duplicate_pipeline_slot(tmp_path):
    source = source_agents_root() / "assistant"
    target = tmp_path / "assistant"
    shutil.copytree(source, target)
    (target / "agent" / "output" / "pipeline.yaml").write_text(
        "serial:\n  - base_output\nparallel:\n  - base_output\n",
        encoding="utf-8",
    )

    with pytest.raises(CoreLoadError, match="duplicate output pipeline slot base_output"):
        CoreLoader().load(target)


def test_loader_rejects_unknown_pipeline_keys(tmp_path):
    source = source_agents_root() / "assistant"
    target = tmp_path / "assistant"
    shutil.copytree(source, target)
    (target / "agent" / "input" / "pipeline.yaml").write_text(
        "serial:\n  - base_input\nunexpected:\n  - profile\n",
        encoding="utf-8",
    )

    with pytest.raises(CoreLoadError, match="invalid input pipeline.yaml key"):
        CoreLoader().load(target)


def test_slot_modules_use_isolated_relative_imports(tmp_path):
    source = source_agents_root() / "assistant"
    target = tmp_path / "assistant"
    shutil.copytree(source, target)
    for slot_id, value in {"relative_a": "A", "relative_b": "B"}.items():
        slot = target / "agent" / "output" / slot_id
        slot.mkdir(parents=True)
        (slot / "slot.yaml").write_text(
            "entrypoint: module:process\n"
            "description: relative import test\n"
            "failure_policy: hard\n"
            "capabilities:\n"
            "  []\n",
            encoding="utf-8",
        )
        (slot / "module.py").write_text(
            "from .helper import VALUE\n\n"
            "def process(ctx):\n"
            "    return VALUE\n",
            encoding="utf-8",
        )
        (slot / "helper.py").write_text(f"VALUE = {value!r}\n", encoding="utf-8")
    (target / "agent" / "output" / "pipeline.yaml").write_text(
        "serial:\n"
        "  - base_output\n"
        "  - relative_a\n"
        "  - relative_b\n"
        "parallel: []\n",
        encoding="utf-8",
    )

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
