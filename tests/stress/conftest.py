from __future__ import annotations

import gc
import json
import os
import platform
import sys
import time
import tracemalloc
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

import pytest

from baseline_support import BaselineContractFailure


BASELINE_SCHEMA = "demiurge-stress-baseline/v1"


def _max_rss_bytes() -> int | None:
    try:
        import resource
    except ImportError:
        return None
    value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    if sys.platform == "darwin":
        return value
    return value * 1024


@dataclass(slots=True)
class BaselineSample:
    observations: dict[str, Any] = field(default_factory=dict)
    measurements: dict[str, int | float | None] = field(default_factory=dict)

    def require(self, condition: bool, contract: str) -> None:
        self.observations["contract"] = contract
        self.observations["contract_satisfied"] = bool(condition)
        if not condition:
            raise BaselineContractFailure(contract)


class BaselineRecorder:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    @contextmanager
    def measure(
        self,
        scenario: str,
        *,
        finding: str | list[str],
        scale: dict[str, int | float | str],
        node_id: str,
    ) -> Iterator[BaselineSample]:
        sample = BaselineSample()
        findings = [finding] if isinstance(finding, str) else list(finding)
        gc.collect()
        tracing_before = tracemalloc.is_tracing()
        if not tracing_before:
            tracemalloc.start()
        tracemalloc.reset_peak()
        rss_before = _max_rss_bytes()
        started = time.perf_counter()
        error_type: str | None = None
        try:
            yield sample
        except BaseException as exc:
            error_type = type(exc).__name__
            raise
        finally:
            wall_seconds = time.perf_counter() - started
            _current, python_peak_bytes = tracemalloc.get_traced_memory()
            if not tracing_before:
                tracemalloc.stop()
            rss_after = _max_rss_bytes()
            sample.measurements.update(
                {
                    "wall_seconds": round(wall_seconds, 6),
                    "python_peak_bytes": int(python_peak_bytes),
                    "max_rss_bytes": rss_after,
                    "max_rss_growth_bytes": (
                        None
                        if rss_before is None or rss_after is None
                        else max(0, rss_after - rss_before)
                    ),
                }
            )
            self.records.append(
                {
                    "node_id": node_id,
                    "scenario": scenario,
                    "finding": findings[0] if len(findings) == 1 else ",".join(findings),
                    "findings": findings,
                    "scale": dict(scale),
                    "measurements": dict(sample.measurements),
                    "observations": dict(sample.observations),
                    "error_type": error_type,
                }
            )

    def payload(self) -> dict[str, Any]:
        return {
            "schema": BASELINE_SCHEMA,
            "python": platform.python_version(),
            "platform": platform.platform(),
            "records": list(self.records),
        }

    def write_optional_output(self) -> Path | None:
        raw_path = os.environ.get("DEMIURGE_BASELINE_OUTPUT", "").strip()
        if not raw_path:
            return None
        path = Path(raw_path).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.payload(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return path


_RECORDER = BaselineRecorder()


class BoundBaselineRecorder:
    def __init__(self, recorder: BaselineRecorder, node_id: str) -> None:
        self._recorder = recorder
        self._node_id = node_id

    def measure(
        self,
        scenario: str,
        *,
        finding: str | list[str],
        scale: dict[str, int | float | str],
    ):
        return self._recorder.measure(
            scenario,
            finding=finding,
            scale=scale,
            node_id=self._node_id,
        )

    def payload(self) -> dict[str, Any]:
        return self._recorder.payload()

    def write_optional_output(self) -> Path | None:
        return self._recorder.write_optional_output()


@pytest.fixture
def baseline_recorder(request) -> BoundBaselineRecorder:
    return BoundBaselineRecorder(_RECORDER, request.node.nodeid)


def pytest_terminal_summary(terminalreporter, exitstatus, config) -> None:
    del exitstatus, config
    if not _RECORDER.records:
        return
    terminalreporter.section("Demiurge stress baseline")
    for record in _RECORDER.records:
        terminalreporter.write_line(json.dumps(record, ensure_ascii=False, sort_keys=True))
    output_path = _RECORDER.write_optional_output()
    if output_path is not None:
        terminalreporter.write_line(f"baseline_output={output_path}")
