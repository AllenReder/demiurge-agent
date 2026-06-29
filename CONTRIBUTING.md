# Contributing

demiurge is in early rapid iteration. Large structural changes can be
acceptable when they simplify the host harness, agent core authored surface, or
testable runtime contracts, but changes should stay grounded in current code.

## Setup

Use `uv` for Python commands:

```bash
uv sync --all-groups
uv run pytest
```

The default local entry is:

```bash
uv run demiurge --provider fake
```

## TUI Development

The terminal UI lives in the root `ui-tui/` TypeScript project. Wheels include a
built JS asset, but source changes to `ui-tui/` must rebuild and copy the asset
used by the Python package:

```bash
cd ui-tui
npm ci
npm test -- --run
npm run typecheck
npm run build
cd ..
cp ui-tui/dist/entry.js demiurge/ui/tui_dist/entry.js
```

## Verification

Use the narrowest useful checks for the change. Common checks are:

```bash
uv run python -m compileall demiurge tests
uv run pytest
cd ui-tui && npm test -- --run
cd ui-tui && npm run typecheck
cd ui-tui && npm run build
uv build
scripts/smoke_wheel_install.sh
git diff --check
```

Documentation-only changes do not need the full runtime test suite, but should
run targeted consistency searches and `git diff --check`.

## Documentation

Update docs in the same change when behavior changes. User-facing behavior,
CLI/configuration, runtime layout, channel behavior, tool behavior, provider
behavior, security policy, package behavior, state/versioning behavior, and
test/gate workflow changes belong in `docs/`.

Documentation under `docs/` is English. The root `README.md` has a
`README.zh-CN.md` mirror and both files should stay structurally aligned.

## Dependencies

Do not add or update Python dependencies without explicit review. demiurge uses
`uv`; do not introduce `requirements.txt`, `requirements.in`, or ad hoc
virtualenv instructions.

v1 candidate agent cores must not automatically add Python dependencies outside
the host `uv` lock. If a feature needs a dependency, document it as a manual
dependency review item.

## Working Tree Hygiene

Check the worktree before editing:

```bash
git status --short
```

Do not revert unrelated changes. If files are already dirty, preserve that work
unless the owner explicitly asks for a revert. Keep commits scoped and stage
only files relevant to the change.
