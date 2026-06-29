# Packages

Package management is a user-facing runtime workflow for installing reusable
catalog packages into an agent core. It is not an agent-callable tool. The main
interface is the standalone interactive CLI:

```bash
uv run demiurge package
```

Scripted subcommands and TUI `/packages` remain secondary entries.

## Catalog Layout

The built-in catalog lives in the source checkout:

```text
agent-catalog/
  catalog.yaml
  input/
  output/
  tool/
  skill/
  lib/
  core/
  packages/
    <package_id>.yaml
```

Wheel installs use bundled fallback resources under
`demiurge/resources/agent-catalog`.

Catalog content is flat by component kind. A package recipe in `packages/`
selects which components to install, how to configure them, and which pipeline
edits to apply.

Supported component kinds are:

- `input`: copied under `agent/input/<slot_id>` and may edit the input pipeline.
- `output`: copied under `agent/output/<slot_id>` and may edit the output pipeline.
- `tool`: copied under `agent/tools/<tool_id>`.
- `skill`: copied under `agent/skills/<skill_id>`.
- `lib`: copied under `agent/lib/<name>` for shared authored code.
- `core`: copied as another runtime active core.

## Package Recipe

A package YAML file is a single installable recipe:

```yaml
schema_version: 2
id: minimax_tts
name: MiniMax TTS
summary: Generate speech audio with MiniMax.
tags:
  - audio
  - tts
options:
  - id: mode
    type: choice
    description: Choose whether TTS speaks the reply directly or summarizes it first.
    default: direct
    choices:
      - value: direct
        description: Generate speech from the assistant reply as-is.
      - value: summary
        description: Summarize the assistant reply before generating speech.
  - id: enable_tool
    type: bool
    description: Also install an authored TTS tool for the agent.
    default: false
components:
  - id: tts_lib
    kind: lib
    source: tts_minimax
    target: agent/lib/tts_minimax
    config:
      provider: tts_minimax
      model: speech-2.8-hd
      voice_setting:
        voice_id: male-qn-qingse
  - id: tts_output
    kind: output
    source: tts_minimax
    target: agent/output/tts_minimax
    pipeline:
      group: parallel
    config:
      summarizer_core: null
    config_when:
      - when:
          mode: summary
        config:
          summarizer_core: tts_summarizer
  - id: tts_summarizer
    kind: core
    source: tts_summarizer
    target_core_id: tts_summarizer
    when:
      mode: summary
```

`tags` are free-form metadata and may contain multiple values. They are used for
search and filtering only; they do not imply conflicts or mutual exclusion.

Option `description` text is shown by the interactive wizard. Choice entries may
be plain strings or objects with `value` and `description`; choice descriptions
are shown next to each selectable value.

`when` includes or skips a component based on resolved option values.
`config_when` conditionally merges extra config into a component config.
Config values may reference options with `${options.<id>}`.

Supported option types are `string`, `bool`, `choice`, `path`, and `secret`.
Secret option values may be written to target component config, but
`packages.yaml` records only `<redacted>`.

## Interactive Wizard

Run:

```bash
uv run demiurge package
```

The wizard first selects a target runtime core, defaulting to `assistant`. It
then offers:

- Search packages
- Browse by tag
- All packages
- Installed packages
- Exit

Install flow shows package details, asks for options, displays an install
preview, then asks for confirmation. Uninstall flow shows the installed package,
displays an uninstall preview, then asks for confirmation.

## Scripted Commands

List packages:

```bash
uv run demiurge package list
uv run demiurge package list --core assistant
uv run demiurge package list --tag tts --json
```

Install a package into an explicit runtime core:

```bash
uv run demiurge package install minimax_tts --core assistant
uv run demiurge package install memory_basic --core assistant
uv run demiurge package install minimax_tts --core assistant --option mode=summary
uv run demiurge package install minimax_tts --core assistant --option enable_tool=true
uv run demiurge package install minimax_tts --core assistant --preview
```

Uninstall:

```bash
uv run demiurge package uninstall minimax_tts --core assistant
uv run demiurge package uninstall memory_basic --core assistant
uv run demiurge package uninstall minimax_tts --core assistant --preview
```

The package command supports install and uninstall only. It does not support
reinstall, config edit, upgrade, rollback, git commits, or agent-callable
package management. `--preview` shows the planned file, core, and pipeline
changes without writing files. Existing target paths are rejected unless the
same source and target are already owned by another installed package, in which
case the target is recorded as reused. Uninstall keeps reused targets until the
final referencing package is removed.

If a required option has no default, scripted install must pass it with
`--option KEY=VALUE`; otherwise the command fails and asks the user to use the
interactive wizard.

## TUI Helper

TUI `/packages` manages the current core only:

```text
/packages
/packages minimax_tts
/packages memory_basic
/packages install minimax_tts
/packages install memory_basic
/packages uninstall minimax_tts
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
package ids, tags, redacted option snapshots, owned or reused component targets,
pipeline edits, warnings, and install time. Component runtime config lives in
each component's own `config.yaml`. Uninstall uses `packages.yaml` to remove
components and pipeline entries.

## Built-In Basic Memory

The built-in catalog includes a local file-backed memory package:

```bash
uv run demiurge package install memory_basic --core assistant
```

It installs:

- `agent/lib/memory_basic`: shared parsing, snapshot, and file-write helpers;
- `agent/input/memory_context`: an input module inserted before `base_input`;
- `agent/tools/memory`: an authored `memory` tool.

The package stores user data outside package-owned component targets:

```text
~/.demiurge/agents/assistant/
  memory/
    USER.md
    MEMORY.md
```

These files are created lazily by the input module or tool. Package uninstall
removes the installed lib/input/tool components and the pipeline entry, but it
does not delete `memory/USER.md` or `memory/MEMORY.md`.

`USER.md` stores stable user profile and preference facts. `MEMORY.md` stores the
agent's project, environment, convention, and tool notes. Entries are separated
with `§`, may be multiline, and are bounded by character limits from
`agent/lib/memory_basic/config.yaml`.

At session start, `memory_context` reads the files, sanitizes prompt-injection or
secret-exfiltration patterns out of the model-facing snapshot, writes
`memory_basic_snapshot.json` under the session root, and injects a short memory
usage guidance plus non-empty memory blocks as transient system context. Later
writes in the same session persist to disk immediately but do not update that
session's snapshot; a new session sees the updated files.

The `memory` tool supports `add`, `replace`, `remove`, and an all-or-nothing
`operations` batch for a single target (`memory` or `user`). Exact duplicate adds
are idempotent. Replacements and removals use a short unique `old_text`
substring. The default authored tool metadata is `risk: medium` and
`approval_policy: auto`.

## Built-In MiniMax TTS

The built-in catalog also includes a TTS package:

```bash
uv run demiurge package install minimax_tts --core assistant
```

Options:

- `mode=direct`: install the TTS output module directly.
- `mode=summary`: also install a `tts_summarizer` child core and configure the
  output module to summarize text before synthesis.
- `enable_tool=true`: also install an authored `text_to_speech` tool and the
  `tts_voice` skill.
- `api_key=<value>`: optional secret written into the shared
  `agent/lib/tts_minimax/config.yaml`. If omitted, the module reads
  `DEMIURGE_MINIMAX_API_KEY`.

The output module and authored tool both reuse shared code from
`agent/lib/tts_minimax`. MiniMax provider and synthesis settings live in the
shared lib config; the output module and `text_to_speech` tool keep only their
slot-local behavior and overrides. The output module runs in the output
`parallel` pipeline so normal text can be delivered before slower speech
generation finishes. The MiniMax Speech T2A non-streaming `t2a_v2` HTTP API
generates local audio artifacts, and both the output module and
`text_to_speech` tool send audio with `history_policy="transient"`, so generated
audio is not written to session history.
