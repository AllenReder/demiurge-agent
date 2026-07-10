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
        "tests/providers",
        "tests/scheduler",
        "tests/scripts",
        "tests/security",
        "tests/storage",
        "tests/tools",
    ),
}
EXPLICIT_PROFILE_TEST_DIRS = {"stress"}


def pytest_args(profile: str, shard: str = "all") -> list[str]:
    if shard not in SHARDS:
        raise ValueError(f"unknown test shard: {shard}")

    args = ["-vv"]
    faulthandler_timeout = 60
    if profile == "stress":
        if shard != "all":
            raise ValueError("stress profile does not support shards")
        args.extend(["-m", "stress", "tests/stress"])
        faulthandler_timeout = 120
    elif profile == "cross-platform-smoke":
        args.extend(["-m", "cross_platform and not stress"])
        args.extend(SHARDS[shard])
    elif profile == "full":
        args.extend(["-m", "not stress"])
        args.extend(SHARDS[shard])
    else:
        raise ValueError(f"unknown test profile: {profile}")
    args.extend(["--durations=20", "-o", f"faulthandler_timeout={faulthandler_timeout}"])
    return args


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Python CI pytest profile.")
    parser.add_argument(
        "--profile",
        choices=["full", "cross-platform-smoke", "stress"],
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

    try:
        profile_args = pytest_args(args.profile, args.shard)
    except ValueError as exc:
        parser.error(str(exc))
    command = [sys.executable, "-m", "pytest", *profile_args]
    return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
