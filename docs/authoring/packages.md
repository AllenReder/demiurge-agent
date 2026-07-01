# Packages

Packages install reusable package repository components into a runtime agent
core. They are a user-facing workflow for composing cores from input modules,
output modules, tools, skills, shared libraries, or child cores.

## Install a Package

Use the interactive wizard:

```bash
uv run demiurge package
```

Scripted install:

```bash
uv run demiurge package list --core assistant
uv run demiurge package list --repo builtin
uv run demiurge package install memory_basic --core assistant --preview
uv run demiurge package install builtin/memory_basic --core assistant --preview
uv run demiurge package install memory_basic --core assistant
```

Uninstall:

```bash
uv run demiurge package uninstall memory_basic --core assistant --preview
uv run demiurge package uninstall memory_basic --core assistant
```

## What Changes

Package install modifies only the target runtime core, for example:

```text
~/.demiurge/agents/assistant/
```

It does not modify repository source templates under `agents/`.

Each target core stores `packages.yaml` at its root. Component configuration
lives in each installed component's `config.yaml`.

## Package Repositories

The built-in package repository lives in:

```text
package-repository/
  repository.yaml
  bootstrap/  # when bootstrap components are present
  input/
  output/
  tool/
  skill/
  lib/
  core/
  packages/
```

Additional repositories are configured in host config:

```yaml
packages:
  repositories:
    builtin:
      type: builtin
    community:
      type: git
      url: https://github.com/user/demiurge-packages.git
      ref: main
      trusted: true
```

Repository aliases are local names. Install references may use
`<repo>/<package_id>`. A bare `<package_id>` works only when it is unique across
configured repositories.

Supported component kinds:

- `bootstrap`
- `input`
- `output`
- `tool`
- `skill`
- `lib`
- `core`

Package recipes select components, collect options, write component config, and
optionally edit bootstrap/input/output pipelines. Bootstrap pipelines are
serial-only.

## Built-In Packages

`memory_basic` installs:

- `agent/lib/memory_basic`
- `agent/bootstrap/memory_basic`
- `agent/tools/memory`

It stores user data outside package-owned component targets:

```text
~/.demiurge/agents/assistant/memory/
  USER.md
  MEMORY.md
```

`conversation_style` installs:

- `agent/input/conversation_style`
- `agent/skills/conversation_style`

It injects transient per-turn response style hints and can auto-load the packaged
style skill. Options choose `concise`, `balanced`, `detailed`, or `technical`
style, plus channel-aware hints.

`context_reseed` installs:

- `agent/lib/context_reseed`
- `agent/bootstrap/context_reseed`
- `agent/output/context_reseed`
- `agent/skills/context_reseed`

It writes a bounded continuity note outside package-owned component targets when
explicitly requested by default, and injects that note as quoted, reference-only
bootstrap context in future sessions:

```text
~/.demiurge/agents/assistant/context/reseed.md
```

TTS provider packages install `agent/lib/tts_<provider>` and
`agent/output/tts_<provider>` by default. `mode=summary` also installs the shared
`tts_summarizer` child core. `enable_tool=true` adds a provider-specific authored
tool and voice skill so multiple TTS providers can coexist in one core:

| Package | Default components | Optional tool |
| --- | --- | --- |
| `minimax_tts` | `agent/lib/tts_minimax`, `agent/output/tts_minimax` | `agent/tools/text_to_speech` |
| `tts_openai` | `agent/lib/tts_openai`, `agent/output/tts_openai` | `agent/tools/text_to_speech_openai` |
| `tts_xai` | `agent/lib/tts_xai`, `agent/output/tts_xai` | `agent/tools/text_to_speech_xai` |
| `tts_gemini` | `agent/lib/tts_gemini`, `agent/output/tts_gemini` | `agent/tools/text_to_speech_gemini` |

Web search provider packages install a provider-owned lib plus the same
model-facing authored tool name, `agent/tools/web_search`:

| Package | Lib | Tool |
| --- | --- | --- |
| `web_search_brave` | `agent/lib/web_search_brave` | `agent/tools/web_search` |
| `web_search_tavily` | `agent/lib/web_search_tavily` | `agent/tools/web_search` |

Because both packages write the same tool target, only one web search provider
package can be installed in a core at a time. To switch providers, uninstall the
current web search package, then install the other one. `web_extract` remains a
host built-in tool for fetching a specific URL; these packages only add search.

Provider secrets are optional package options and can also be read from
environment variables. Prefer environment variables for non-plaintext storage:
`api_key` option values are written into the installed runtime component's
`config.yaml` so the component can run without host CLI state. Built-in
provider-specific environment variables include `DEMIURGE_MINIMAX_API_KEY`,
`DEMIURGE_OPENAI_API_KEY`, `DEMIURGE_XAI_API_KEY`, `DEMIURGE_GEMINI_API_KEY`,
`DEMIURGE_BRAVE_SEARCH_API_KEY`, and `DEMIURGE_TAVILY_API_KEY`;
provider-standard fallbacks include `OPENAI_API_KEY`, `XAI_API_KEY`,
`GEMINI_API_KEY`, `GOOGLE_API_KEY`, `BRAVE_SEARCH_API_KEY`, `BRAVE_API_KEY`,
and `TAVILY_API_KEY` where supported by the package.

## Success Check

```bash
uv run demiurge package list --core assistant
uv run demiurge init --check
uv run demiurge --provider fake
```

Use `/tools` after installing a tool package.

## Boundary

Package management is not an agent-callable model tool. It is a CLI/TUI helper
for user-controlled runtime core edits. It does not install Python dependencies
or change the host lock file. Package recipes may document manual dependency
review items, which are shown as warnings.
