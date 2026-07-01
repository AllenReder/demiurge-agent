#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

cd "$ROOT"

uv build --wheel --out-dir "$TMP/dist"
wheel="$(find "$TMP/dist" -maxdepth 1 -name 'demiurge-*.whl' -print -quit)"
if [[ -z "$wheel" ]]; then
  echo "wheel not found under $TMP/dist" >&2
  exit 1
fi

uv run python - "$wheel" <<'PY'
import sys
import zipfile

wheel = sys.argv[1]
required = {
    "demiurge/resources/agents/agent.yaml",
    "demiurge/resources/agents/assistant/agent.yaml",
    "demiurge/resources/agents/evolver/agent.yaml",
    "demiurge/resources/package-repository/repository.yaml",
    "demiurge/ui/tui_dist/entry.js",
}

with zipfile.ZipFile(wheel) as archive:
    names = set(archive.namelist())

missing = sorted(required - names)
if missing:
    print("wheel is missing required files:", file=sys.stderr)
    for name in missing:
        print(f"  {name}", file=sys.stderr)
    raise SystemExit(1)
PY

uv run --isolated --with "$wheel" demiurge --home "$TMP/home" init --json >/dev/null
test -f "$TMP/home/agents/agent.yaml"
test -f "$TMP/home/agents/assistant/agent.yaml"
test -f "$TMP/home/agents/evolver/agent.yaml"

uv run --isolated --with "$wheel" demiurge --home "$TMP/home" --provider fake --help >/dev/null
uv run --isolated --with "$wheel" demiurge --home "$TMP/home" init --help >/dev/null
uv run --isolated --with "$wheel" demiurge --home "$TMP/home" package list --json >/dev/null
