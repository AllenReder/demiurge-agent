# Contributing

demiurge is an early alpha Python agent framework. The project is still moving
quickly, so changes should stay grounded in the current code and documentation
rather than preserving obsolete internal layouts by default.

Large structural changes can be acceptable when they simplify the host harness,
agent core authored surface, runtime layout, or testable contracts. They should
still be proposed and reviewed as focused changes.

## Project Boundaries

The host owns the runtime harness: sessions, turns, context assembly, provider
calls, tool execution, approvals, state, promotion, and rollback.

Agent cores own the authored surface: instructions, skills, tool adapters,
hooks, connections, schedules, input modules, output modules, tests, and
evolution policy.

When a change affects this boundary, runtime layout, agent core schema, package
recipes, safety policy, provider behavior, state/versioning behavior, or release
workflow, open an issue first unless a maintainer has already asked for the
specific PR.

## Good First Contributions

Direct pull requests are usually fine for:

- Documentation fixes.
- Reproducible bug fixes.
- Focused tests for current behavior.
- Small CLI, TUI, package, or authoring usability improvements.

Please open an issue before working on:

- New or updated Python, Node, system, or provider dependencies.
- Runtime layout or agent core schema changes.
- Host harness, tool runtime, approval, provider, state, promotion, or rollback
  boundary changes.
- Large refactors or compatibility layers.
- Release process, packaging, or installation behavior changes.

## Pull Requests

Keep pull requests scoped to one feature, fix, cleanup, or documentation pass.
Avoid mixing unrelated refactors with behavior changes.

Every behavior change should explain:

- What user-visible behavior changed.
- Which runtime or authoring boundary is affected.
- Whether docs were updated, or why docs are not needed.
- Which verification commands were run.

Maintainers may squash commits when merging. Commit history inside a PR does not
need to mirror the final release history.

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

Use the narrowest useful checks for the change.

Documentation-only changes:

```bash
git diff --check
```

Run targeted `rg` searches when docs rename commands, concepts, options, or
runtime paths.

Focused Python changes:

```bash
uv run pytest tests/path/to/test_file.py
```

Broad Python runtime changes:

```bash
uv run python -m compileall demiurge tests
uv run pytest
```

TUI changes:

```bash
cd ui-tui && npm test -- --run
cd ui-tui && npm run typecheck
cd ui-tui && npm run build
cd ..
cmp ui-tui/dist/entry.js demiurge/ui/tui_dist/entry.js
```

Packaging or release-path changes:

```bash
uv build
scripts/smoke_wheel_install.sh
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

Candidate agent cores must not automatically add Python dependencies outside the
host `uv` lock. If a feature needs a dependency, document it as a manual
dependency review item.

## Working Tree Hygiene

Check the worktree before editing:

```bash
git status --short
```

Do not revert unrelated changes. If files are already dirty, preserve that work
unless the owner explicitly asks for a revert. Keep commits scoped and stage
only files relevant to the change.

Do not include secrets, API keys, tokens, private local paths, or large generated
logs in issues or pull requests.
