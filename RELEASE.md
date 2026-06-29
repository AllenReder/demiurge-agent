# Release Checklist

demiurge is alpha software. Release notes must state that APIs, runtime layout,
and authoring contracts may still change.

## Repository Metadata

Before making the repository public, confirm GitHub shows:

- description: local-first Python agent harness with authored agent cores;
- topics: `agent`, `agent-framework`, `llm`, `local-first`, `automation`;
- website/docs link: repository README or published docs URL;
- license detected as Apache-2.0;
- security policy detected from `SECURITY.md`;
- contributing link detected from `CONTRIBUTING.md`.

## Local Release Checks

Run from a clean worktree, excluding unrelated local files:

```bash
git status --short
uv run python -m compileall demiurge tests
uv run pytest
cd ui-tui && npm test -- --run
cd ui-tui && npm run typecheck
cd ui-tui && npm run build
cd ..
cmp ui-tui/dist/entry.js demiurge/ui/tui_dist/entry.js
uv build
uv run python -m zipfile -l dist/demiurge-0.1.1-py3-none-any.whl | rg 'demiurge/resources/(agents|agent-catalog)|demiurge/ui/tui_dist/entry.js'
tar -tzf dist/demiurge-0.1.1.tar.gz | rg '^demiurge-[^/]+/\.temp/' && exit 1 || true
scripts/smoke_wheel_install.sh
git diff --check
```

## Version and Tag Flow

1. Update `pyproject.toml` version.
2. Update release notes with user-visible changes, breaking changes, known
   limitations, and verification commands.
3. Rebuild artifacts with `uv build`.
4. Run `scripts/smoke_wheel_install.sh` against the built wheel.
5. Create a signed or annotated tag when possible:

```bash
git tag -a v0.1.1 -m "demiurge v0.1.1"
```

6. Create a GitHub release and attach `dist/*.whl` and `dist/*.tar.gz`.

## First Public Alpha Notes

Release notes should explicitly mention:

- alpha/developer-preview status;
- host-owned harness and Agent Core boundary;
- default runtime home `~/.demiurge`;
- default workspace `~/.demiurge/workspace`;
- no container sandbox in v1;
- code slots run in the host-shared Python environment by default;
- Telegram is long-polling only;
- package catalog is local-only;
- LLM-driven evolver core is not complete.

## PyPI Decision

Do not publish the first public alpha to PyPI automatically. Treat PyPI as a
separate release decision after:

- package name ownership is confirmed;
- GitHub release artifacts pass wheel smoke testing;
- README, license, security policy, and repository metadata are visible;
- at least one install path is tested outside the source checkout.
