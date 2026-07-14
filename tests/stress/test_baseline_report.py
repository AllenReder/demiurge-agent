from __future__ import annotations

import json

import pytest


pytestmark = pytest.mark.stress


def test_structured_baseline_output_can_be_persisted(
    tmp_path,
    monkeypatch,
    baseline_recorder,
):
    output_path = tmp_path / "baseline.json"
    monkeypatch.setenv("DEMIURGE_BASELINE_OUTPUT", str(output_path))
    with baseline_recorder.measure(
        "baseline_report_contract",
        finding="DG-P0-T03",
        scale={"records": 1},
    ) as sample:
        sample.observations["structured"] = True

    written = baseline_recorder.write_optional_output()
    payload = json.loads(output_path.read_text(encoding="utf-8"))

    assert written == output_path.resolve()
    assert payload["schema"] == "demiurge-stress-baseline/v1"
    record = next(
        record
        for record in payload["records"]
        if record["scenario"] == "baseline_report_contract"
    )
    assert record["node_id"].endswith("test_structured_baseline_output_can_be_persisted")
    assert record["findings"] == ["DG-P0-T03"]
    assert record["observations"]["structured"] is True
