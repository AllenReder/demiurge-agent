# Update and Doctor

Use this page when updating a managed checkout or checking whether runtime
agent cores still match source templates.

## Update a Managed Checkout

```bash
~/.demiurge/demiurge-agent/.venv/bin/demiurge update
```

`demiurge update` runs `git fetch`, fast-forwards or checks out an optional ref,
syncs dependencies with `uv`, then runs a read-only runtime drift check unless
`--skip-init-check` is provided.

It does not overwrite live runtime cores.

## Check Runtime Drift

```bash
uv run demiurge init --check
uv run demiurge doctor
```

Both are read-only checks. They compare the selected runtime core and source
templates and report drift.

## Refresh Runtime Templates

Use refresh only when you intentionally want to replace runtime templates from
the source templates:

```bash
uv run demiurge init --refresh assistant
uv run demiurge init --refresh evolver
uv run demiurge init --refresh global
uv run demiurge init --refresh all
```

Existing runtime templates are backed up under `~/.demiurge/history/` before
refresh.

## Success Check

After update or refresh:

```bash
uv run demiurge init --check
uv run demiurge --provider fake
```

The fake-provider TUI should start and `/status` should show the expected core.

## Boundary

Normal package installs and hand-authored module edits modify live runtime
cores. `demiurge update` intentionally leaves those live cores alone.
