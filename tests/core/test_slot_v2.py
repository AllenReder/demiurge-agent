import shutil

import pytest
import yaml

from demiurge.app import source_agents_root
from demiurge.core import CoreLoadError, CoreLoader, load_slot_callable


def test_slot_manifests_and_pipelines_load_source_core():
    core = CoreLoader().load(source_agents_root() / "assistant")

    assert [slot.slot_id for slot in core.bootstrap_pipeline.serial] == ["session_context"]
    assert [slot.slot_id for slot in core.input_pipeline.serial] == ["base_input"]
    assert [slot.slot_id for slot in core.output_pipeline.serial] == ["base_output"]
    assert core.input_slots[0].entrypoint == "module:process"


def test_slot_manifests_reject_global_duplicate_ids(tmp_path):
    target = tmp_path / "assistant"
    shutil.copytree(source_agents_root() / "assistant", target)
    duplicate = target / "agent" / "output" / "base_input"
    duplicate.mkdir()
    (duplicate / "slot.yaml").write_text("failure_policy: soft\ncapabilities: []\n", encoding="utf-8")

    with pytest.raises(CoreLoadError, match="duplicate slot id base_input"):
        CoreLoader().load(target)


def test_slot_manifest_supports_entrypoint_override(tmp_path):
    target = tmp_path / "assistant"
    shutil.copytree(source_agents_root() / "assistant", target)
    custom = target / "agent" / "output" / "custom"
    custom.mkdir()
    (custom / "handler.py").write_text("def go(ctx):\n    return 'ok'\n", encoding="utf-8")
    (custom / "slot.yaml").write_text("entrypoint: handler:go\nfailure_policy: soft\n", encoding="utf-8")
    pipelines_path = target / "agent" / "pipelines.yaml"
    raw = yaml.safe_load(pipelines_path.read_text(encoding="utf-8"))
    raw["output"]["serial"].append("custom")
    pipelines_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    core = CoreLoader().load(target)
    slot = next(slot for slot in core.output_slots if slot.slot_id == "custom")

    assert load_slot_callable(slot)(None) == "ok"


def test_slot_manifest_rejects_legacy_run_alias(tmp_path):
    target = tmp_path / "assistant"
    shutil.copytree(source_agents_root() / "assistant", target)
    custom = target / "agent" / "output" / "custom"
    custom.mkdir()
    (custom / "module.py").write_text("def process(ctx):\n    return 'ok'\n", encoding="utf-8")
    (custom / "slot.yaml").write_text("run: module:process\nfailure_policy: soft\n", encoding="utf-8")

    with pytest.raises(CoreLoadError, match="invalid slot.yaml agent/output/custom/slot.yaml key\\(s\\): run"):
        CoreLoader().load(target)
