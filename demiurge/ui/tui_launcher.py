from __future__ import annotations

import argparse
from importlib.resources import files
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


def run_tui_from_args(args: argparse.Namespace) -> None:
    node = shutil.which("node")
    if not node:
        raise SystemExit("demiurge TUI requires Node.js. Install Node.js 20 or newer to run the bundled Ink frontend.")
    entry, command = _resolve_tui_entry(node)
    env = os.environ.copy()
    env["DEMIURGE_TUI_GATEWAY_CONFIG"] = json.dumps(_gateway_config(args), ensure_ascii=False)
    env["DEMIURGE_TUI_GATEWAY_PYTHON"] = sys.executable
    env.setdefault("PYTHONPATH", _pythonpath())
    completed = subprocess.run(command, env=env)
    if completed.returncode:
        raise SystemExit(completed.returncode)


def _resolve_tui_entry(node: str) -> tuple[Path, list[str]]:
    root = Path(__file__).resolve().parents[2]
    dist_entry = root / "ui-tui" / "dist" / "entry.js"
    source_entry = root / "ui-tui" / "src" / "entry.tsx"
    if dist_entry.exists():
        return dist_entry, [node, str(dist_entry)]
    packaged_entry = _packaged_tui_entry()
    if packaged_entry.exists():
        return packaged_entry, [node, str(packaged_entry)]
    if source_entry.exists() and (root / "ui-tui" / "node_modules" / "tsx").exists():
        return source_entry, [node, "--import", "tsx", str(source_entry)]
    raise SystemExit(
        "demiurge TUI frontend asset is missing. In a source checkout run "
        "`cd ui-tui && npm ci && npm run build`, or reinstall a wheel that includes the bundled Ink TUI asset."
    )


def _packaged_tui_entry() -> Path:
    try:
        resource = files("demiurge.ui.tui_dist").joinpath("entry.js")
    except ModuleNotFoundError:
        return Path("__missing_tui_dist__")
    return Path(str(resource))


def _gateway_config(args: argparse.Namespace) -> dict[str, Any]:
    def path_value(value: Any) -> str | None:
        return str(value) if value is not None else None

    return {
        "home": path_value(args.home),
        "core": args.core,
        "agents_root": path_value(args.agents_root),
        "provider": args.provider,
        "model": args.model,
        "fake_script": path_value(args.fake_script),
        "workspace": path_value(args.workspace),
        "workspace_fallback": path_value(Path.cwd().resolve()),
        "timezone": getattr(args, "timezone", None),
        "session": args.session,
        "resume": args.resume,
        "tool_display": args.tool_display,
        "busy_mode": getattr(args, "channel_busy_mode", None),
    }


def _pythonpath() -> str:
    src = str(Path(__file__).resolve().parents[2])
    existing = os.environ.get("PYTHONPATH")
    return f"{src}{os.pathsep}{existing}" if existing else src
