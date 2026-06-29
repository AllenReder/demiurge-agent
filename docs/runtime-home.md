# Runtime Home

The default runtime home is:

```text
~/.demiurge/
```

Typical layout:

```text
~/.demiurge/
  config.yaml
  demiurge-agent/
  agents/
    agent.yaml
    assistant/
      packages.yaml
    evolver/
  history/
  registry/
  runs/
  scheduler/
    <core_id>/
      state.json
      runs.jsonl
      lock
  sessions/
    <session_id>/
      session.json
      messages.jsonl
      events.jsonl
      artifacts/
  state/
    <core_id>.json
    proposals.jsonl
  workspace/
```

- `config.yaml`: host-level runtime config. `demiurge init` creates it when
  missing. It stores default core, default workspace, interactive channel busy
  mode, and local TUI layout/theme preferences.
- `demiurge-agent/`: optional managed git checkout created by
  `scripts/install.sh`. `demiurge update` updates this checkout and its uv
  environment.
- `agents/agent.yaml`: global fallback config. It allows `model`, `ui`, and
  `approval`.
- `agents/<core_id>/`: live runtime agent core.
- `agents/<core_id>/packages.yaml`: package install records for that core.
  Secret option values are redacted.
- `history/`: backups created before init refresh, promotion, or rollback.
- `registry/`: active version pointers for each core.
- `runs/`: evolution candidates, gate results, and reports.
- `scheduler/<core_id>/`: scheduler state, run log, and lock file.
- `sessions/`: durable session metadata, messages, events, and artifacts.
- `state/`: per-core runtime state and state proposal log.
- `workspace/`: default tool workspace when no workspace override is set.

Normal `uv run demiurge` fills in missing fallback, assistant, and evolver
runtime templates without overwriting user edits. Explicit `uv run demiurge
init` creates missing host config and refreshes runtime templates after backup.
`init --refresh` refreshes selected templates and does not overwrite
`config.yaml`.

`demiurge update` only updates the managed checkout and uv dependencies, then
runs read-only `init --check`. It does not refresh live cores under `agents/`.
