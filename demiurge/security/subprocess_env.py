from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Mapping


ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_PASSTHROUGH_NAMES = frozenset(
    {
        "PATH",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TERM",
        "COLORTERM",
        "TMPDIR",
        "TMP",
        "TEMP",
        "TZ",
        "SYSTEMROOT",
        "SYSTEMDRIVE",
        "COMSPEC",
        "PATHEXT",
        "WINDIR",
    }
)
RESERVED_SUBPROCESS_ENV_TARGETS = _PASSTHROUGH_NAMES | frozenset(
    {
        "HOME",
        "USERPROFILE",
        "SHELL",
        "BASH_ENV",
        "ENV",
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "DYLD_INSERT_LIBRARIES",
        "DYLD_LIBRARY_PATH",
        "PYTHONHOME",
        "PYTHONPATH",
        "NODE_OPTIONS",
        "RUBYOPT",
        "PERL5OPT",
    }
)


def build_sanitized_subprocess_env(
    host_env: Mapping[str, str],
    overlay: Mapping[str, str],
    *,
    home: Path,
) -> dict[str, str]:
    """Build a minimal child environment without ambient Host credentials."""

    env: dict[str, str] = {}
    for key, value in host_env.items():
        normalized = str(key).upper()
        if normalized not in _PASSTHROUGH_NAMES or not value:
            continue
        target = "PATH" if normalized == "PATH" else str(key)
        env.setdefault(target, str(value))

    controlled_home = home.expanduser().resolve()
    env["HOME"] = str(controlled_home)
    if os.name == "nt":
        env["USERPROFILE"] = str(controlled_home)
    env.update({str(key): str(value) for key, value in overlay.items()})
    return env


def ensure_subprocess_home(home: Path) -> Path:
    controlled_home = home.expanduser().resolve()
    controlled_home.mkdir(parents=True, exist_ok=True, mode=0o700)
    return controlled_home


__all__ = [
    "ENV_NAME_RE",
    "RESERVED_SUBPROCESS_ENV_TARGETS",
    "build_sanitized_subprocess_env",
    "ensure_subprocess_home",
]
