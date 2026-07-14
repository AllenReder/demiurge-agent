from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def load_ci_tests_module():
    script = Path(__file__).resolve().parents[2] / "scripts" / "run_python_ci_tests.py"
    spec = importlib.util.spec_from_file_location("run_python_ci_tests", script)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_pytest_args_full_profile_runs_current_full_suite():
    runner = load_ci_tests_module()

    assert runner.pytest_args("full") == [
        "-vv",
        "-m",
        "not stress",
        "--durations=20",
        "-o",
        "faulthandler_timeout=60",
    ]


def test_pytest_args_full_profile_can_select_runtime_shard():
    runner = load_ci_tests_module()

    assert runner.pytest_args("full", "runtime") == [
        "-vv",
        "-m",
        "not stress",
        "tests/runtime",
        "--durations=20",
        "-o",
        "faulthandler_timeout=60",
    ]


def test_windows_shards_cover_current_test_directories():
    runner = load_ci_tests_module()
    tests_root = Path(__file__).resolve().parents[1]
    current_dirs = {path.name for path in tests_root.iterdir() if path.is_dir()}

    shard_dirs = {
        Path(test_path).name
        for shard in ("channels", "runtime", "packages", "rest")
        for test_path in runner.SHARDS[shard]
    }

    assert shard_dirs | runner.EXPLICIT_PROFILE_TEST_DIRS == current_dirs


def test_pytest_args_rest_shard_covers_remaining_test_directories():
    runner = load_ci_tests_module()

    assert runner.pytest_args("full", "rest") == [
        "-vv",
        "-m",
        "not stress",
        "tests/app",
        "tests/cli",
        "tests/core",
        "tests/diagnostics",
        "tests/evolution",
        "tests/providers",
        "tests/scheduler",
        "tests/scripts",
        "tests/security",
        "tests/storage",
        "tests/tools",
        "--durations=20",
        "-o",
        "faulthandler_timeout=60",
    ]


def test_pytest_args_cross_platform_smoke_filters_marker():
    runner = load_ci_tests_module()

    assert runner.pytest_args("cross-platform-smoke") == [
        "-vv",
        "-m",
        "cross_platform and not stress",
        "--durations=20",
        "-o",
        "faulthandler_timeout=60",
    ]


def test_pytest_args_stress_profile_runs_only_explicit_stress_suite():
    runner = load_ci_tests_module()

    assert runner.pytest_args("stress") == [
        "-vv",
        "-m",
        "stress",
        "tests/stress",
        "--durations=20",
        "-o",
        "faulthandler_timeout=120",
    ]


def test_pytest_args_stress_profile_rejects_regular_shards():
    runner = load_ci_tests_module()

    try:
        runner.pytest_args("stress", "runtime")
    except ValueError as exc:
        assert str(exc) == "stress profile does not support shards"
    else:
        raise AssertionError("stress profile accepted a regular test shard")


def test_main_reports_stress_shard_as_cli_usage_error(capsys):
    runner = load_ci_tests_module()

    with pytest.raises(SystemExit) as exc_info:
        runner.main(["--profile", "stress", "--shard", "runtime"])

    assert exc_info.value.code == 2
    assert "stress profile does not support shards" in capsys.readouterr().err


def test_main_runs_pytest_module_and_returns_exit_code(monkeypatch):
    runner = load_ci_tests_module()
    calls = []

    class Result:
        returncode = 7

    def fake_run(command, check):
        calls.append({"command": command, "check": check})
        return Result()

    monkeypatch.setattr(runner.subprocess, "run", fake_run)

    assert runner.main(["--profile", "cross-platform-smoke", "--shard", "channels"]) == 7
    assert calls == [
        {
            "command": [
                runner.sys.executable,
                "-m",
                "pytest",
                "-vv",
                "-m",
                "cross_platform and not stress",
                "tests/channels",
                "--durations=20",
                "-o",
                "faulthandler_timeout=60",
            ],
            "check": False,
        }
    ]
