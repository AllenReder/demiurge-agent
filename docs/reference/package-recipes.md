# Package Recipe Reference

Package recipes live under:

```text
package-repository/packages/<package_id>.yaml
```

They describe installable component sets for runtime cores.

## Recipe Shape

```yaml
schema_version: 2
id: minimax_tts
name: MiniMax TTS
summary: Generate speech audio with MiniMax.
tags:
  - audio
  - tts
manual_dependencies: []
options:
  - id: mode
    type: choice
    description: Choose direct or summary mode.
    default: direct
    choices:
      - value: direct
        description: Generate speech from the assistant reply.
      - value: summary
        description: Summarize before generating speech.
components:
  - id: tts_output
    kind: output
    source: tts_minimax
    target: agent/output/tts_minimax
    pipeline:
      group: parallel
```

## Option Types

Supported types:

- `string`
- `bool`
- `choice`
- `path`
- `secret`

Secret option values may be written to target component config, but
`packages.yaml` records only `<redacted>`.

## Component Kinds

| Kind | Target |
| --- | --- |
| `bootstrap` | `agent/bootstrap/<slot_id>` |
| `input` | `agent/input/<slot_id>` |
| `output` | `agent/output/<slot_id>` |
| `tool` | `agent/tools/<tool_id>` |
| `skill` | `agent/skills/<skill_id>` |
| `lib` | `agent/lib/<name>` |
| `core` | another runtime active core identified by `target_core_id` |

For core-local component kinds, `target` is the runtime-core-relative path to
write. For `kind: core`, use `target_core_id`; `target` is ignored.

## Conditions and Config

`when` includes or skips a component based on resolved option values.
`config_when` conditionally merges extra config into a component config.
Config values can reference options with `${options.<id>}`.

## Validation Rules

- Component sources must stay inside the package repository.
- Component sources cannot be symlinks.
- Existing target paths are rejected unless reused by another installed package
  with the same source and target.
- Pipeline edits are allowed only for bootstrap/input/output components.
- Bootstrap pipeline edits are serial-only.
- `manual_dependencies` entries are warnings only; Demiurge does not install
  Python dependencies or edit the host lock file.

## Recipe Examples

`conversation_style` is a small input + skill recipe: it inserts an input module
before `base_input`, writes option-backed `config.yaml`, and installs a
progressive style skill.

`context_reseed` combines lib + output + bootstrap + skill components: the output
slot refreshes a bounded continuity note, while the bootstrap slot reads that
note as reference-only session context.

A core component recipe uses `target_core_id`:

```yaml
components:
  - id: tts_summarizer
    kind: core
    source: tts_summarizer
    target_core_id: tts_summarizer
```

## Boundary

Recipes describe runtime file installation. They do not install Python
dependencies or edit the host uv lock.
