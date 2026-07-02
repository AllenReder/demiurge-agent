import shutil

import pytest
import yaml

from demiurge.app import source_agents_root
from demiurge.core import CoreLoadError, CoreLoader, load_slot_callable


def test_slots_yaml_phase_group_manifest_loads_source_core():
    core = CoreLoader().load(source_agents_root() / "assistant")

    assert [slot.slot_id for slot in core.bootstrap_pipeline.serial] == ["session_context"]
    assert [slot.slot_id for slot in core.input_pipeline.serial] == ["base_input"]
    assert [slot.slot_id for slot in core.output_pipeline.serial] == ["base_output"]
    assert core.input_slots[0].entrypoint == "module:process"


def test_slots_yaml_rejects_global_duplicate_ids(tmp_path):
    target = tmp_path / "assistant"
    shutil.copytree(source_agents_root() / "assistant", target)
    slots_path = target / "agent" / "slots.yaml"
    raw = yaml.safe_load(slots_path.read_text(encoding="utf-8"))
    raw["slots"]["output"]["base_input"] = {"failure": "soft", "capabilities": []}
    slots_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    with pytest.raises(CoreLoadError, match="duplicate slot id base_input"):
        CoreLoader().load(target)


def test_slots_yaml_supports_run_path_override(tmp_path):
    target = tmp_path / "assistant"
    shutil.copytree(source_agents_root() / "assistant", target)
    custom = target / "agent" / "output" / "custom"
    custom.mkdir()
    (custom / "handler.py").write_text("def go(ctx):\n    return 'ok'\n", encoding="utf-8")
    slots_path = target / "agent" / "slots.yaml"
    raw = yaml.safe_load(slots_path.read_text(encoding="utf-8"))
    raw["slots"]["output"]["custom"] = {"run": "agent/output/custom/handler.py:go", "failure": "soft"}
    raw["pipelines"]["output"]["serial"].append("custom")
    slots_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    core = CoreLoader().load(target)
    slot = next(slot for slot in core.output_slots if slot.slot_id == "custom")

    assert load_slot_callable(slot)(None) == "ok"
