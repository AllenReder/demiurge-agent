# Packages

Package management is a user-facing runtime workflow for installing reusable
catalog components into an agent core through presets. It is not an
agent-callable tool in v1. The main interface is the standalone interactive CLI:

```bash
uv run demiurge package
```

Scripted subcommands and TUI `/packages` remain secondary entries.

## Catalog Layout

The built-in catalog lives in the source checkout:

```text
agent-catalog/
  catalog.yaml
  features/
  components/
  presets/
```

Wheel installs use bundled fallback resources under
`demiurge/resources/agent-catalog`.

Future community catalogs should use the same root structure. `components/`
stores uninstalled input/output/core templates. `presets/` describes which
components to copy, default config writes, pipeline insertion, and tags.

Tag conflicts are advisory. The wizard shows warnings and asks for
confirmation. Scripted install continues and returns warnings in the result.

## Interactive Wizard

Run:

```bash
uv run demiurge package
```

The wizard first selects a target runtime core, defaulting to `assistant`. It
then offers catalog browsing, installed package view, and exit. Catalog browsing
shows preset summaries, tags, components, options, and install action.
Installed package view reads the target core's `packages.yaml` and can
uninstall recorded packages.

## Scripted Commands

List presets:

```bash
uv run demiurge package list
uv run demiurge package list --core assistant
```

Install a preset into an explicit runtime core:

```bash
uv run demiurge package install tts_only --core assistant
```

Uninstall:

```bash
uv run demiurge package uninstall tts_only --core assistant
```

v1 supports install and uninstall only. It does not support reinstall, config
edit, upgrade, rollback, git commits, or agent-callable package tools. Existing
target paths are rejected by default; no automatic overwrite or intelligent
merge is attempted.

Scripted `install` does not accept option values. It uses preset defaults. If a
required option has no default, the command fails and asks the user to use the
interactive wizard.

## Options and Writes

Presets can declare options and writes:

```yaml
options:
  - id: api_key
    type: secret
    prompt: MiniMax API key
    default_env: DEMIURGE_MINIMAX_API_KEY
writes:
  - option: api_key
    component: tts_minimax
    path: api_key
```

Supported option types are `string`, `bool`, `choice`, `path`, and `secret`.
`writes` only write answers into the target component `config.yaml`; package
Python install hooks are not executed. Secret option values may be written to
target config, but `packages.yaml` records only `<redacted>`.

## TUI Helper

TUI `/packages` manages the current core only:

```text
/packages
/packages tts_only
/packages install tts_only
/packages uninstall tts_only
```

Use the standalone `demiurge package` wizard for cross-core installs or installs
that need option prompts.

## Runtime State

Install only modifies the runtime active core, for example:

```text
~/.demiurge/agents/assistant/
```

It does not modify source templates under repository `agents/`.

Each target core stores `packages.yaml` at its root. It records installed
presets, component target paths, pipeline insertion info, tags, warnings, and
option snapshots. Component runtime config lives in each component's own
`config.yaml`. Uninstall uses `packages.yaml` to remove components and pipeline
entries.

## Built-In TTS Presets

The built-in catalog currently includes:

- `tts_only`: installs a parent output module that generates a local audio
  artifact from `ctx.output.content` and delivers it with
  `ctx.output.send_audio(...)`.
- `tts_summary`: also installs a `tts_summarizer` child core. The parent output
  module first calls `ctx.agents.run("tts_summarizer", ...)`, then generates and
  delivers audio.

The built-in TTS output component is `tts_minimax`. It uses the MiniMax Speech
T2A non-streaming `t2a_v2` HTTP API to generate local audio artifacts. It reads
`DEMIURGE_MINIMAX_API_KEY` by default or accepts an optional secret through the
wizard. The real value is written to the target component config, while the
package record stores `<redacted>`.

MiniMax audio delivery uses `history_policy="transient"` and does not write to
session history.
