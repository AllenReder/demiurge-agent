# Contributing

Demiurge is a `0.x` alpha Python agent framework. The project is public, but
the runtime, authored Agent Core contracts, package repository behavior, and
release process may still change before a stable `1.0` line.

Contributions are welcome when they are grounded in the current repository
state and scoped to one clear outcome. During `0.x`, maintainers may choose
simple breaking changes over compatibility layers when the change makes the
host/runtime boundary easier to understand and test.

## Governance

Maintainers make final decisions on project direction, release timing, support
scope, and whether a change belongs in the current alpha line. The default
decision criteria are:

- keep host-owned effects explicit and reviewable;
- keep the authored Agent Core surface file-backed and understandable;
- prefer narrow changes that can be validated in the checkout;
- avoid new dependencies unless they are intentionally reviewed;
- document user-visible behavior in the same change that introduces it.

Open an issue before starting work that changes project direction, public
contracts, runtime layout, security policy, package installation behavior, or
release automation. Direct pull requests are fine for small fixes when the
intent is obvious.

## Project Boundaries

The host owns the runtime harness: sessions, turns, context assembly, provider
calls, tool execution, approvals, state, versioning, promotion, and rollback.

Agent Cores own the authored surface: `agent.yaml`, instructions, skills, tool
adapters, schedules, input modules, output modules, tests, and evolution policy.

Changes that cross this boundary need extra review. This includes changes to
tool capabilities, provider behavior, approval policy, state/versioning,
package recipes, dependency handling, and any code path that executes authored
modules.

## Good First Contributions

Good first contributions are:

- documentation fixes;
- reproducible bug fixes;
- focused tests for current behavior;
- small CLI, TUI, package, or authoring usability improvements;
- release-note corrections for already-published behavior.

Please open an issue before working on:

- new or updated Python, Node, system, or provider dependencies;
- runtime layout or Agent Core schema changes;
- host harness, tool runtime, approval, provider, state, promotion, or rollback
  boundary changes;
- compatibility layers for old internal layouts;
- release process, packaging, or installation behavior changes;
- security-sensitive behavior or public security policy changes.

## Pull Requests

Keep pull requests scoped to one feature, fix, cleanup, or documentation pass.
Avoid mixing unrelated refactors with behavior changes.

Every behavior-changing pull request should explain:

- what user-visible behavior changed;
- which runtime or authoring boundary is affected;
- whether documentation was updated, or why no documentation is needed;
- which verification commands were run.

Maintainers may squash commits when merging. Commit history inside a pull
request does not need to mirror the final release history.

## Setup

Use `uv` for Python commands:

```bash
uv sync --all-groups
uv run pytest
```

The default local entry point is:

```bash
uv run demiurge --provider fake
```

Do not add `requirements.txt`, `requirements.in`, or ad hoc virtualenv
instructions. The source checkout and `uv.lock` are the dependency source of
truth for development.

## TUI Development

The terminal UI lives in the root `ui-tui/` TypeScript project. The Python
package includes a built JavaScript asset, so source changes to `ui-tui/` must
rebuild and copy the asset used by the Python package:

```bash
cd ui-tui
npm ci
npm test
npm run typecheck
npm run build
cd ..
node -e "require('fs').copyFileSync('ui-tui/dist/entry.js', 'demiurge/ui/tui_dist/entry.js')"
node --check ui-tui/dist/entry.js
node -e "const fs = require('fs'); const built = fs.readFileSync('ui-tui/dist/entry.js'); const packaged = fs.readFileSync('demiurge/ui/tui_dist/entry.js'); if (!built.equals(packaged)) process.exit(1);"
```

## Verification

Use the narrowest useful checks for the change.

Documentation-only changes:

```bash
git diff --check -- README.md README.zh-CN.md docs website CONTRIBUTING.md SECURITY.md RELEASE.md
```

Run targeted `rg` searches when docs rename commands, concepts, options,
runtime paths, release links, or security claims.

Focused Python changes:

```bash
uv run pytest tests/path/to/test_file.py
```

Broad Python runtime changes:

```bash
uv run python -m compileall demiurge tests
uv run python scripts/run_python_ci_tests.py --profile full
uv run python scripts/smoke_managed_install.py
```

To mirror the Windows CI pytest shards locally:

```bash
uv run python scripts/run_python_ci_tests.py --profile full --shard channels
uv run python scripts/run_python_ci_tests.py --profile full --shard runtime
uv run python scripts/run_python_ci_tests.py --profile full --shard packages
uv run python scripts/run_python_ci_tests.py --profile full --shard rest
```

For OS-sensitive smoke coverage without running the full suite:

```bash
uv run python scripts/run_python_ci_tests.py --profile cross-platform-smoke
```

TUI changes:

```bash
cd ui-tui && npm test
cd ui-tui && npm run typecheck
cd ui-tui && npm run build
cd ..
node --check ui-tui/dist/entry.js
node -e "const fs = require('fs'); const built = fs.readFileSync('ui-tui/dist/entry.js'); const packaged = fs.readFileSync('demiurge/ui/tui_dist/entry.js'); if (!built.equals(packaged)) process.exit(1);"
```

Release-path changes should preserve the default notes/tag-only release policy.
CI and release validation should cover the public support surface:

- Python 3.11, 3.12, and 3.13 on Ubuntu with the full Python suite;
- Python 3.11 on Windows with the full Python suite split across parallel shards;
- TUI smoke checks on Node.js 20 and 24 on Ubuntu and Windows;
- managed checkout installation smoke checks on Ubuntu and Windows.

Build artifacts are optional and must be requested explicitly:

```bash
uv build
scripts/smoke_wheel_install.sh
```

## Documentation

Update documentation in the same change when user-visible behavior changes.
CLI/configuration, runtime layout, channel behavior, tool behavior, provider
behavior, security policy, package behavior, state/versioning behavior, and
test/gate workflow changes belong in the manual or public policy files.

Documentation under `docs/` is English. The root `README.md` has a
`README.zh-CN.md` mirror and both files should stay structurally aligned when
entry-point copy changes.

## Dependencies

Do not add or update Python dependencies without explicit review. Demiurge uses
`uv`; the lock file must stay aligned with `pyproject.toml`.

Candidate Agent Cores must not automatically add Python dependencies outside
the host lock file. If a feature needs a dependency, document it as a manual
dependency review item.

## Working Tree Hygiene

Check the worktree before editing:

```bash
git status --short
```

Do not revert unrelated changes. If files are already dirty, preserve that work
unless the owner explicitly asks for a revert. Keep commits scoped and stage
only files relevant to the change.

Do not include secrets, API keys, tokens, private local paths, or large
generated logs in issues or pull requests.
