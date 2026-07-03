---
title: Write a Package Recipe
description: Design packages/<package_id>.yaml with options, components, pipeline placement, config overlays, and conditions.
---

# Write a Package Recipe

Use this guide when you already know the behavior you want to package and need
to write `packages/<package_id>.yaml`.

A package recipe tells Demiurge which authored-surface files to install into a
runtime Agent Core. It does not define the runtime code itself. Slot modules,
tool modules, skills, libraries, child cores, MCP manifests, schedules, and
default `config.yaml` files live in component source directories.

For a complete field dictionary, see
[Package Recipe Reference](../reference/package-recipes.md). If you still need
the repository root, start with
[Create an External Package Repository](../tutorials/external-package-repository.md).

## 1. Choose the Capability Shape

Pick component kinds from the behavior you want to install:

| Goal | Component kind |
| --- | --- |
| Add context before a session or turn | `bootstrap` |
| Transform user input before the model request | `input` |
| React to assistant output after the model response | `output` |
| Add a model-callable authored tool | `tool` |
| Add reusable author guidance | `skill` |
| Add package-owned helper code or config | `lib` |
| Add a package-owned child Agent Core | `core` |
| Add an MCP server declaration | `mcp` |
| Add a schedule declaration | `schedule` |

Use `lib` for shared code that slot modules and tools import. Use `core` only
when the package needs a separate runtime Agent Core, such as a summarizer.

## 2. Start with Package Identity

Create one recipe under:

```text
packages/<package_id>.yaml
```

Start with stable identity fields:

```yaml
schema_version: 1
id: reply_style
name: Reply Style
summary: Add a package-provided reply style hint.
tags:
  - input
  - style
capabilities: []
manual_dependencies: []
components: []
```

Use a stable `id`. Installed runtime cores record it in `packages.yaml`, and
repository-qualified refs use it:

```text
community/reply_style
```

Use `manual_dependencies` only for warnings that need human review. Demiurge
does not install Python dependencies and does not edit `uv.lock`.

## 3. Design Install Options

Add `options` only for values that should be chosen at install time. Keep fixed
behavior in component source files or default `config.yaml`.

```yaml
options:
  - id: api_key
    type: secret
    prompt: Provider API key
    description: Optional direct API key; leave empty to read PROVIDER_API_KEY.
    required: false
    default: null
  - id: mode
    type: choice
    prompt: Runtime mode
    description: Choose whether to install only direct output or also a child core.
    default: direct
    choices:
      - value: direct
        description: Use the assistant reply as-is.
      - value: summary
        description: Summarize the assistant reply before output.
  - id: enable_tool
    type: bool
    prompt: Add authored tool
    description: Also install a model-callable tool.
    default: false
```

Supported option types are `string`, `bool`, `choice`, `path`, and `secret`.
`secret` values may be written into installed component config, but the install
record stores `<redacted>`.

Use `${options.<id>}` inside component `config` or `config_when` to render
resolved option values.

## 4. Add Components

Each component entry points at a source under the matching repository root:

```yaml
components:
  - id: reply_style_input
    kind: input
    source: reply_style
    target: agent/input/reply_style
    pipeline:
      group: serial
      append: true
```

This installs files from:

```text
input/reply_style/
```

into:

```text
agent/input/reply_style/
```

`bootstrap`, `input`, and `output` components require `slot.yaml` and a
`pipeline` entry. `tool` components require `tool.yaml`. `lib` and `skill`
components are copied as directories. `core` components create a package-owned
runtime core. `mcp` and `schedule` components install one YAML declaration file.

## 5. Add Config Overlays

Use `config` when the package recipe should patch a component's default
`config.yaml` with install options.

The component source must contain `config.yaml`:

```text
lib/web_search_brave/
  config.yaml
  provider.py
```

The recipe can render an option into that config:

```yaml
components:
  - id: web_search_lib
    kind: lib
    source: web_search_brave
    target: agent/lib/web_search_brave
    config:
      api_key: ${options.api_key}
```

Exact option references preserve the resolved value type. For example,
`${options.enable_tool}` stays boolean when used as the whole value. Option
references embedded inside longer strings render as text.

Use `config_when` when a component is always installed but only some modes need
extra config:

```yaml
components:
  - id: tts_output
    kind: output
    source: tts_minimax
    target: agent/output/tts_minimax
    pipeline:
      group: parallel
      append: true
    config_when:
      - when:
          mode: summary
        config:
          summarizer_core: tts_summarizer
          summary: MiniMax summarized TTS audio
```

`config` and `config_when` are not valid for `core` components.

## 6. Gate Optional Components

Use `when` when a component should be installed only for some option values:

```yaml
components:
  - id: tts_tool
    kind: tool
    source: text_to_speech_minimax
    target: agent/tools/text_to_speech
    when:
      enable_tool: true
  - id: tts_summarizer
    kind: core
    source: tts_summarizer
    target_core_id: tts_summarizer
    when:
      mode: summary
  - id: tts_voice_skill
    kind: skill
    source: tts_voice
    target: agent/skills/tts_voice
    when:
      enable_tool: true
```

Conditions are exact matches against resolved option values. Every option id
used in `when` or `config_when.when` must be declared in `options`.

## 7. Common Recipe Patterns

### Simple Input Slot

Use this when a package only adds an input transformation:

```yaml
schema_version: 1
id: reply_style
name: Reply Style
summary: Add a package-provided reply style hint.
tags:
  - input
  - style
capabilities: []
components:
  - id: reply_style_input
    kind: input
    source: reply_style
    target: agent/input/reply_style
    pipeline:
      group: serial
      append: true
```

### Provider Package with Secret Config

Use this when the package needs provider credentials and shared code:

```yaml
schema_version: 1
id: web_search_brave
name: Brave Web Search
summary: Search the web with Brave Search through a package-owned web_search tool.
tags:
  - web
  - search
  - provider:brave
options:
  - id: api_key
    type: secret
    prompt: Brave Search API key
    description: Optional direct Brave Search API key; leave empty to read DEMIURGE_BRAVE_SEARCH_API_KEY.
    required: false
    default: null
capabilities:
  - network.fetch
components:
  - id: web_search_tool
    kind: tool
    source: web_search_brave
    target: agent/tools/web_search
  - id: web_search_lib
    kind: lib
    source: web_search_brave
    target: agent/lib/web_search_brave
    config:
      api_key: ${options.api_key}
```

### Optional Child Core and Tool

Use this when a mode installs extra authored surface:

```yaml
schema_version: 1
id: tts_minimax
name: MiniMax TTS
summary: Generate speech audio with MiniMax, either directly or through a summarizer core.
tags:
  - audio
  - tts
  - provider:minimax
options:
  - id: mode
    type: choice
    prompt: TTS mode
    description: Choose whether the output module speaks the final text directly or first runs a summarizer core.
    default: direct
    choices:
      - value: direct
        description: Generate speech from the assistant reply as-is.
      - value: summary
        description: Summarize the assistant reply before generating speech.
  - id: enable_tool
    type: bool
    prompt: Add agent TTS tool
    description: Also install an authored tool so the agent can generate speech on demand.
    default: false
components:
  - id: tts_output
    kind: output
    source: tts_minimax
    target: agent/output/tts_minimax
    pipeline:
      group: parallel
      append: true
    config_when:
      - when:
          mode: summary
        config:
          summarizer_core: tts_summarizer
  - id: tts_tool
    kind: tool
    source: text_to_speech_minimax
    target: agent/tools/text_to_speech
    when:
      enable_tool: true
  - id: tts_summarizer
    kind: core
    source: tts_summarizer
    target_core_id: tts_summarizer
    when:
      mode: summary
```

## 8. Verify the Recipe

Validate that the repository and recipe load:

```bash
uv run demiurge package repo add ~/demiurge-packages --alias local --trust
uv run demiurge package list --repo local
```

Preview before writing into a runtime core:

```bash
uv run demiurge package install local/reply_style --core assistant --preview
```

If the package installs a slot, tool, MCP declaration, schedule declaration, or
child core, check that the target runtime still loads:

```bash
uv run demiurge init --check
```

When the recipe is ready and you want to share it, continue with
[Publish a Package Repository](publish-package-repository.md).
