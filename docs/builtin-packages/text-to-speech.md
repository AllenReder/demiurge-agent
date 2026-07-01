---
sidebar_position: 3
title: Text-to-Speech Packages
description: Install built-in text-to-speech packages for assistant audio output.
---

# Text-to-Speech Packages

Built-in text-to-speech packages generate audio from assistant replies. Each
provider installs one package-owned output slot and can optionally install an
authored tool for on-demand speech generation.

Install one TTS output package per core unless you intentionally want multiple
audio providers to run after every assistant response.

## Packages

| Package | Provider |
| --- | --- |
| `tts_minimax` | MiniMax |
| `tts_openai` | OpenAI |
| `tts_gemini` | Gemini |
| `tts_xai` | xAI |

## Install

Preview first:

```bash
uv run demiurge package install tts_minimax --core assistant --preview
```

Install direct output mode:

```bash
uv run demiurge package install tts_minimax --core assistant
```

Install summary mode:

```bash
uv run demiurge package install tts_minimax \
  --core assistant \
  --option mode=summary
```

Install with an on-demand TTS tool:

```bash
uv run demiurge package install tts_minimax \
  --core assistant \
  --option enable_tool=true
```

## Options

| Option | Default | Description |
| --- | --- | --- |
| `mode` | `direct` | `direct` speaks the assistant reply as-is. `summary` installs the shared `tts_summarizer` core and synthesizes its summary instead. |
| `enable_tool` | `false` | Installs the authored `text_to_speech` tool for on-demand speech generation. |
| `api_key` | unset | Direct provider API key. Leave empty to use environment variables. |

## Credential Environment Variables

| Package | Environment variables |
| --- | --- |
| `tts_minimax` | `DEMIURGE_MINIMAX_API_KEY`; optional `DEMIURGE_MINIMAX_GROUP_ID` |
| `tts_openai` | `DEMIURGE_OPENAI_API_KEY` or `OPENAI_API_KEY` |
| `tts_gemini` | `DEMIURGE_GEMINI_API_KEY`, `GEMINI_API_KEY`, or `GOOGLE_API_KEY` |
| `tts_xai` | `DEMIURGE_XAI_API_KEY` or `XAI_API_KEY` |

## Runtime Behavior

The installed output slot runs in the output pipeline after `base_output`. In
`direct` mode it sends the assistant reply text to the provider. In `summary`
mode it first runs the shared `tts_summarizer` child core, then synthesizes the
summary.

Generated audio is written under the workspace:

```text
.demiurge-tts/
```

The output slot emits an audio artifact delivery. Channels that support audio
can forward that artifact to the user.

## Tools

When `enable_tool=true`, the package installs an authored TTS tool:

| Package | Tool |
| --- | --- |
| `tts_minimax` | `text_to_speech` |
| `tts_openai` | `text_to_speech` |
| `tts_gemini` | `text_to_speech` |
| `tts_xai` | `text_to_speech` |

Use the tool when the model should generate a specific audio clip on demand
rather than speaking every assistant response through the output slot.

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
