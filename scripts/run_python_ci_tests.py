from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Iterable

SHARDS: dict[str, tuple[str, ...]] = {
    "all": (),
    "channels": ("tests/channels",),
    "runtime": ("tests/runtime",),
    "packages": ("tests/packages",),
    "rest": (
        "tests/app",
        "tests/cli",
        "tests/core",
        "tests/diagnostics",
        "tests/evolution",
        "tests/scheduler",
        "tests/scripts",
        "tests/security",
        "tests/tools",
    ),
}


def pytest_args(profile: str, shard: str = "all") -> list[str]:
    if shard not in SHARDS:
        raise ValueError(f"unknown test shard: {shard}")

    args = ["-vv"]
    if profile == "cross-platform-smoke":
        args.extend(["-m", "cross_platform"])
    elif profile != "full":
        raise ValueError(f"unknown test profile: {profile}")
    args.extend(SHARDS[shard])
    args.extend(["--durations=20", "-o", "faulthandler_timeout=60"])
    return args


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Python CI pytest profile.")
    parser.add_argument(
        "--profile",
        choices=["full", "cross-platform-smoke"],
        default="full",
        help="pytest profile to execute",
    )
    parser.add_argument(
        "--shard",
        choices=sorted(SHARDS),
        default="all",
        help="pytest shard to execute",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    command = [sys.executable, "-m", "pytest", *pytest_args(args.profile, args.shard)]
    return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
