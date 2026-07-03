---
title: Install Packages
description: Use the interactive package manager, then script package list, install, and uninstall operations when needed.
---

# Install Packages

Use packages when an existing Agent Core needs an optional capability such as
memory, speech-to-text, text-to-speech, web search, style hints, MCP
declarations, or schedules.

The normal entrypoint is the interactive package manager:

```bash
uv run demiurge package
```

It lets you select a runtime core, browse packages, filter by repository or tag,
preview changes, install packages, uninstall installed packages, and manage
package repositories. Preview and list views are read-only.

Use the subcommands on this page when you want a repeatable command for a
script, runbook, or issue comment.

## Before You Install

Start from an initialized runtime core:

```bash
uv run demiurge init
```

List available packages:

```bash
uv run demiurge package list --core assistant
```

Filter by repository or tag:

```bash
uv run demiurge package list --repo builtin
uv run demiurge package list --tag memory
uv run demiurge package list --tag stt
```

If two repositories contain the same package id, use a repository-qualified ref:

```bash
builtin/memory_basic
```

If you have edited `~/.demiurge/agents` directly, package install and uninstall
save those local agent edits first as a separate core revision. The package
operation then runs in its own Git transaction. Use `uv run demiurge core diff`
to inspect unsaved edits before installing.

## Preview an Install

Preview first. The preview shows which targets will be written or reused and
which manual warnings apply.

```bash
uv run demiurge package install memory_basic --core assistant --preview
```

Provider packages usually need credentials. Pass package options with repeated
`--option` flags, or leave optional secrets unset so the installed component can
read its documented environment variables at runtime:

```bash
uv run demiurge package install tts_minimax \
  --core assistant \
  --option mode=summary \
  --option enable_tool=true \
  --preview
```

## Install a Package

Install after the preview looks right:

```bash
uv run demiurge package install memory_basic --core assistant
```

Install a repository-qualified package when needed:

```bash
uv run demiurge package install builtin/memory_basic --core assistant
```

Install options are resolved once during installation. Secret option values may
be written into the installed component config, but `packages.yaml` stores only
redacted option snapshots. A successful install is committed to the runtime
core Git repository and reports the new core revision. If local agent edits
were present before installation, Demiurge reports a package revision after
first saving those edits in a separate revision.

## What Installation Writes

Package installation writes into the active runtime core, not the source
template checkout. For the default core this is under:

```text
~/.demiurge/agents/assistant/
```

Installation can copy package-owned components into:

```text
agent/bootstrap/
agent/input/
agent/output/
agent/tools/
agent/skills/
agent/lib/
```

It can also create package-owned child cores, MCP declaration YAML files, and
schedule declaration YAML files.

When a package installs a `bootstrap`, `input`, or `output` slot, Demiurge also
updates the target core's `agent/pipelines.yaml`. Bootstrap slots are always in
the serial bootstrap pipeline. Input and output slots can be serial or parallel,
depending on the package recipe.

The install record is written to:

```text
~/.demiurge/agents/<core-id>/packages.yaml
```

Packages do not install Python dependencies and do not edit `uv.lock`.
`manual_dependencies` are warnings for a human dependency review.

`packages.yaml` is provenance only. It records installed targets and
file/tree hashes so Demiurge can report drift, but runtime behavior comes from
the live files under `~/.demiurge/agents/`.

## Built-in Package Families

The built-in repository currently ships:

| Family | Packages |
| --- | --- |
| Memory | `memory_basic`, `memory_honcho` |
| Context | `context_reseed` |
| Communication | `conversation_style` |
| Web search | `web_search_brave`, `web_search_tavily` |
| Speech-to-text | `stt_openai`, `stt_groq`, `stt_deepgram`, `stt_assemblyai`, `stt_gemini`, `stt_dashscope`, `stt_baidu`, `stt_tencent` |
| Text-to-speech | `tts_minimax`, `tts_openai`, `tts_gemini`, `tts_xai` |

See the built-in package pages for package behavior and options:

- [memory_basic](../builtin-packages/memory/memory_basic.md)
- [memory_honcho](../builtin-packages/memory/memory_honcho.md)
- [context_reseed](../builtin-packages/context-reseed.md)
- [conversation_style](../builtin-packages/conversation-style.md)
- [Web Search Packages](../builtin-packages/web-search.md)
- [Speech-to-Text Packages](../builtin-packages/speech-to-text.md)
- [Text-to-Speech Packages](../builtin-packages/text-to-speech.md)

## Switch a Provider Package

Some provider packages intentionally share the same target. For example, all STT
packages target `agent/input/speech_to_text`, and both web search packages
target `agent/tools/web_search`.

Uninstall the current provider package before installing another one:

```bash
uv run demiurge package uninstall web_search_brave --core assistant --preview
uv run demiurge package uninstall web_search_brave --core assistant
uv run demiurge package install web_search_tavily --core assistant --preview
uv run demiurge package install web_search_tavily --core assistant
```

Use the same pattern for STT packages:

```bash
uv run demiurge package uninstall stt_openai --core assistant
uv run demiurge package install stt_gemini --core assistant
```

## Uninstall a Package

Preview removal:

```bash
uv run demiurge package uninstall memory_basic --core assistant --preview
```

Uninstall:

```bash
uv run demiurge package uninstall memory_basic --core assistant
```

Uninstall removes package-owned component targets, removes package-owned
pipeline entries for `bootstrap`, `input`, and `output` slots, and updates
`packages.yaml`. A successful uninstall is also committed to the runtime core
Git repository.

Like install, uninstall first saves unrelated local agent edits as their own
revision. It does not mix manual edits into the package uninstall commit.

If package-owned files have drifted since installation, uninstall refuses by
default:

```bash
uv run demiurge package uninstall memory_basic --core assistant
```

Use `--force-drift` only when you intentionally want to remove drifted
package-owned targets:

```bash
uv run demiurge package uninstall memory_basic --core assistant --force-drift
```

Uninstall does not remove data written outside package-owned targets. For
example, memory data, generated audio, context notes, caches, and provider
outbox files remain unless you remove them yourself.

## Verify

List installed packages for the core:

```bash
uv run demiurge package list --core assistant
```

Check that the runtime core still loads:

```bash
uv run demiurge core check
```

Run a fake-provider turn:

```bash
uv run demiurge --provider fake
```

If a package installs tools, inspect the TUI tool list:

```text
/tools
```

## Manage Repositories

Use the interactive manager for repository operations:

```bash
uv run demiurge package
```

For scripted repository commands, see
[Manage Package Repositories](manage-package-repositories.md).

If you are creating a repository for other users to install from, see
[Publish a Package Repository](publish-package-repository.md).

## Boundaries

Package management is a user-controlled CLI workflow. It is not an
agent-callable model tool.

Packages install authored-surface files. The host still owns sessions, provider
calls, approvals, MCP transport, schedule execution, and dependency policy.
