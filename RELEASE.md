# Release Guide

Demiurge is `0.x` alpha software. Release notes must state that APIs, runtime
layout, and authored Agent Core contracts may still change before `1.0`.

The default public release is notes/tag-only: update the version and release
notes, create an annotated tag, and publish a GitHub Release whose body comes
from `docs/releases/<version>.md`. Do not build, upload, or attach wheel/sdist
assets unless artifact publication is explicitly requested for that release.

## Repository Metadata

Before a release, confirm GitHub shows:

- description: local-first Python agent harness with authored Agent Cores;
- topics: `agent`, `agent-framework`, `llm`, `local-first`, `automation`;
- website/docs link: repository README or published docs URL;
- license detected as Apache-2.0;
- security policy detected from `SECURITY.md`;
- contributing link detected from `CONTRIBUTING.md`.

## Release Unit

Prepare release changes as one unit:

- `pyproject.toml` version;
- `demiurge/__init__.py` `__version__`;
- `uv.lock` entry for the local `demiurge` package;
- `docs/releases/<version>.md`;
- latest-release links in `README.md`, `README.zh-CN.md`, `docs/README.md`,
  `website/docusaurus.config.ts`, and the zh-CN footer translation;
- any user-facing documentation required by the behavior being released.

The release note heading must be:

```markdown
# Demiurge <version>
```

## Official Workflow

Use the manual GitHub Actions workflow from `main` after the release unit is
merged:

```bash
gh workflow run Release \
  --ref main \
  -f version=<version> \
  -f prerelease=false \
  -f build_artifacts=false \
  -f repair_existing_tag=false
```

The workflow has five phases:

1. **Preflight** resolves the version and tag, requires a `main` branch release,
   checks duplicate tag/release state, and allows repair mode only when the
   existing tag points at the current commit.
2. **Validate** checks version consistency, release-note heading, public release
   links, Python gates, TUI gates, website build, and whitespace.
3. **Optional artifacts** runs `uv build` and `scripts/smoke_wheel_install.sh`
   only when `build_artifacts=true`, then uploads the `dist/` files for the
   publish phase.
4. **Publish** creates the annotated tag unless repair mode is being used, then
   creates the GitHub Release from `docs/releases/<version>.md`. Artifact files
   are attached only when `build_artifacts=true`.
5. **Verify** checks the remote tag, GitHub Release fields, and the release
   asset list. When `build_artifacts=false`, assets must be empty.

## Repairing an Existing Tag

Use repair mode only when a release tag already exists at the intended commit
but the GitHub Release object is missing or needs to be created by the workflow:

```bash
gh workflow run Release \
  --ref main \
  -f version=<version> \
  -f prerelease=false \
  -f build_artifacts=false \
  -f repair_existing_tag=true
```

Repair mode still validates the release unit and refuses to run unless the
remote tag resolves to the current workflow commit. It does not overwrite an
existing GitHub Release.

## Local Validation

The workflow is the preferred release gate. When validating locally before
dispatching it, use the same checks:

```bash
git status --short
uv run python -m compileall demiurge tests
uv run python scripts/run_python_ci_tests.py --profile full
uv run python scripts/run_python_ci_tests.py --profile full --shard channels
uv run python scripts/run_python_ci_tests.py --profile full --shard runtime
uv run python scripts/run_python_ci_tests.py --profile full --shard packages
uv run python scripts/run_python_ci_tests.py --profile full --shard rest
uv run demiurge --help
uv run demiurge init --help
uv run python scripts/smoke_managed_install.py
(cd ui-tui && npm ci && npm run typecheck && npm test && npm run build)
node --check ui-tui/dist/entry.js
node -e "const fs = require('fs'); const built = fs.readFileSync('ui-tui/dist/entry.js'); const packaged = fs.readFileSync('demiurge/ui/tui_dist/entry.js'); if (!built.equals(packaged)) process.exit(1);"
(cd website && npm ci && npm run build)
git diff --check -- ':!demiurge/ui/tui_dist/entry.js'
```

The GitHub release workflow is the full cross-platform gate: Python validation
runs the full suite on Ubuntu for Python 3.11, 3.12, and 3.13, and runs the
full suite on Windows for Python 3.11 split across parallel shards. TUI
validation runs on Ubuntu and Windows for Node.js 20 and 24.

For artifact releases only, also run:

```bash
uv build
scripts/smoke_wheel_install.sh
```

## Manual Fallback

Use this path only if GitHub Actions is unavailable.

1. Confirm the release unit is complete and the worktree is clean except for
   intentional local-only files.
2. Run the local validation checks above.
3. Create an annotated tag:

```bash
version="$(uv run python -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')"
git tag -a "v${version}" -m "demiurge ${version}"
```

4. Push and verify the remote tag:

```bash
git push origin "v${version}"
git ls-remote --tags origin "v${version}*"
```

5. Create the GitHub Release with the release note file as the release body:

```bash
gh release create "v${version}" \
  --title "Demiurge ${version}" \
  --notes-file "docs/releases/${version}.md"
```

6. Verify the published release. For the default notes/tag-only path, the asset
   list must be empty:

```bash
gh release view "v${version}" --json tagName,name,isDraft,isPrerelease,publishedAt,url,assets
```

## Release Notes

Release notes should include:

- alpha/developer-preview status;
- user-visible changes;
- fixes;
- breaking changes, if any;
- known limitations;
- verification commands.

Current alpha limitations that may need to appear in release notes include:

- code slots run in the host-shared Python environment by default;
- Candidate Agent Core evolution cannot add dependencies automatically;
- Telegram is long-polling only;
- package repositories can be local, built-in, or explicitly trusted Git/path
  sources;
- GitHub Releases do not attach wheel or sdist artifacts by default.

## PyPI Decision

Do not publish to PyPI automatically. Treat PyPI as a separate release decision
after:

- package name ownership is confirmed;
- wheel/sdist artifact production is intentionally enabled and tested;
- README, license, security policy, and repository metadata are visible;
- at least one install path is tested outside the source checkout.
