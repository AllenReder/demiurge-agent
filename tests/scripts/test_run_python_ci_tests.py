from __future__ import annotations

import importlib.util
from pathlib import Path


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
        "--durations=20",
        "-o",
        "faulthandler_timeout=60",
    ]


def test_pytest_args_full_profile_can_select_runtime_shard():
    runner = load_ci_tests_module()

    assert runner.pytest_args("full", "runtime") == [
        "-vv",
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

    assert shard_dirs == current_dirs


def test_pytest_args_rest_shard_covers_remaining_test_directories():
    runner = load_ci_tests_module()

    assert runner.pytest_args("full", "rest") == [
        "-vv",
        "tests/app",
        "tests/cli",
        "tests/core",
        "tests/diagnostics",
        "tests/evolution",
        "tests/scheduler",
        "tests/scripts",
        "tests/security",
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
        "cross_platform",
        "--durations=20",
        "-o",
        "faulthandler_timeout=60",
    ]


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
                "cross_platform",
                "tests/channels",
                "--durations=20",
                "-o",
                "faulthandler_timeout=60",
            ],
            "check": False,
        }
    ]
