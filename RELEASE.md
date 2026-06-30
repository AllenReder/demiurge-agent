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
version="$(uv run python -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')"
wheel="dist/demiurge-${version}-py3-none-any.whl"
sdist="dist/demiurge-${version}.tar.gz"
test -f "$wheel"
test -f "$sdist"
uv run python -m zipfile -l "$wheel" | rg 'demiurge/resources/(agents|agent-catalog)|demiurge/ui/tui_dist/entry.js'
if tar -tzf "$sdist" | rg '^demiurge-[^/]+/\.temp/'; then
  echo "sdist includes .temp content" >&2
  exit 1
fi
scripts/smoke_wheel_install.sh
git diff --check
```

## Version and Tag Flow

The preferred release path is the manual GitHub Actions workflow
`.github/workflows/release.yml`. Run it from `main` after the version bump,
release notes, and release-link documentation updates are already merged.

The workflow verifies that:

- `pyproject.toml`, `demiurge.__version__`, and the requested version match;
- `docs/releases/<version>.md` exists and has the matching release heading;
- the website release link points at `docs/releases/<version>`;
- the target tag and GitHub Release do not already exist;
- Python tests, TUI checks, website build, package build, and wheel smoke test
  all pass.

If all checks pass, the workflow creates an annotated `v<version>` tag, pushes
it to `origin`, creates the GitHub Release with
`docs/releases/<version>.md` as the release body, attaches the wheel and sdist,
and verifies the remote tag and release object.

Manual fallback, if GitHub Actions is unavailable:

1. Update `pyproject.toml`, `demiurge/__init__.py`, and `uv.lock` to the same
   version.
2. Add or update `docs/releases/<version>.md` with user-visible changes,
   breaking changes, known limitations, and verification commands. Keep this
   checklist accurate when the release process changes.
3. Update website or README release links that should point at the latest
   release notes.
4. Rebuild artifacts with `uv build`.
5. Run `scripts/smoke_wheel_install.sh` against the built wheel.
6. Create a signed or annotated tag when possible:

```bash
version="$(uv run python -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')"
git tag -a "v${version}" -m "demiurge ${version}"
```

7. Push the tag and verify it exists remotely:

```bash
git push origin "v${version}"
git ls-remote --tags origin "v${version}*"
```

8. Create a GitHub release for that tag, pass the release note file as the
   release body, attach `dist/*.whl` and `dist/*.tar.gz`, and verify it:

```bash
gh release create "v${version}" \
  --title "Demiurge ${version}" \
  --notes-file "docs/releases/${version}.md" \
  "dist/demiurge-${version}-py3-none-any.whl" \
  "dist/demiurge-${version}.tar.gz"
gh release view "v${version}" --json tagName,name,isDraft,isPrerelease,publishedAt,url
```

For PEP 440 pre-releases such as `0.2.0a1`, use the clearer public tag spelling
described in the versioning policy if needed.

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
