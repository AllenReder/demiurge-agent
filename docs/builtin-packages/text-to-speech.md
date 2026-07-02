---
sidebar_position: 3
title: Text-to-Speech Packages
description: Install built-in text-to-speech packages for assistant audio output.
---

# Text-to-Speech Packages

Built-in text-to-speech packages generate audio from assistant replies. Each
provider installs an output slot and can optionally install an authored
`text_to_speech` tool for on-demand generation.

Install one TTS output package per core unless you intentionally want multiple
audio providers to run after every assistant response.

## Packages

| Package | Provider | Capabilities |
| --- | --- | --- |
| `tts_minimax` | MiniMax | `network.fetch`, `agents.run:tts_summarizer` |
| `tts_openai` | OpenAI | `network.fetch`, `agents.run:tts_summarizer` |
| `tts_gemini` | Gemini | `network.fetch`, `agents.run:tts_summarizer` |
| `tts_xai` | xAI | `network.fetch`, `agents.run:tts_summarizer` |

## Install

Use the interactive manager:

```bash
uv run demiurge package
```

Or preview and install with subcommands:

```bash
uv run demiurge package install tts_minimax --core assistant --preview
uv run demiurge package install tts_minimax --core assistant
```

Install summary mode:

```bash
uv run demiurge package install tts_minimax \
  --core assistant \
  --option mode=summary
```

Install with the on-demand tool:

```bash
uv run demiurge package install tts_minimax \
  --core assistant \
  --option enable_tool=true
```

## Shared Options

All built-in TTS packages support:

| Option | Default | Description |
| --- | --- | --- |
| `mode` | `direct` | `direct` speaks the assistant reply as-is. `summary` installs the shared `tts_summarizer` child core and speaks its summary. Choices: `direct`, `summary`. |
| `enable_tool` | `false` | Installs the authored `text_to_speech` tool and a provider-specific TTS skill. |
| `api_key` | unset | Optional direct provider API key. Leave empty to use environment variables. |

In `summary` mode, the package installs or reuses a package-owned child core
named `tts_summarizer`.

## Credential Environment Variables

| Package | Environment variables |
| --- | --- |
| `tts_minimax` | `DEMIURGE_MINIMAX_API_KEY`; optional `DEMIURGE_MINIMAX_GROUP_ID` |
| `tts_openai` | `DEMIURGE_OPENAI_API_KEY` or `OPENAI_API_KEY` |
| `tts_gemini` | `DEMIURGE_GEMINI_API_KEY`, `GEMINI_API_KEY`, or `GOOGLE_API_KEY` |
| `tts_xai` | `DEMIURGE_XAI_API_KEY` or `XAI_API_KEY` |

## Runtime Behavior

The installed output slot runs in the parallel output pipeline after the model
response is available. In `direct` mode it strips common Markdown from the
assistant reply, truncates to the configured provider limit, sends the text to
the provider, and emits an audio artifact.

In `summary` mode it first calls the `tts_summarizer` child core, then
synthesizes the returned summary.

Generated audio is written under the workspace:

```text
.demiurge-tts/
```

Channels that support audio can forward the audio artifact to the user.

## Tool

When `enable_tool=true`, the package installs:

```text
agent/tools/text_to_speech/
```

The model can use that tool to generate a specific audio clip on demand instead
of speaking every assistant response through the output slot.

Because all provider packages target `agent/tools/text_to_speech`, install only
one tool-enabled TTS provider in a core at a time.

## Verify

List installed packages:

```bash
uv run demiurge package list --core assistant
```

Run a turn and inspect the workspace:

```text
.demiurge-tts/
```

If `enable_tool=true`, inspect the tool registry in the TUI:

```text
/tools
```

## Uninstall

```bash
uv run demiurge package uninstall tts_minimax --core assistant --preview
uv run demiurge package uninstall tts_minimax --core assistant
```

Uninstall removes package-owned output slots, provider libs, optional tool and
skill files, and package-owned pipeline entries. It does not remove generated
audio under `.demiurge-tts/`.
