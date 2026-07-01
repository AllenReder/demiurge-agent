---
sidebar_position: 2
title: Speech-to-Text Packages
description: Install built-in speech-to-text packages for audio attachment transcription.
---

# Speech-to-Text Packages

Built-in speech-to-text packages transcribe audio or video attachments before
the model request. They install an input slot at `agent/input/speech_to_text`,
so install only one STT provider package in a core at a time.

## Packages

| Package | Provider |
| --- | --- |
| `stt_openai` | OpenAI |
| `stt_groq` | Groq |
| `stt_deepgram` | Deepgram |
| `stt_assemblyai` | AssemblyAI |
| `stt_gemini` | Gemini |
| `stt_dashscope` | Alibaba Cloud Model Studio |
| `stt_baidu` | Baidu Cloud Speech Recognition |
| `stt_tencent` | Tencent Cloud ASR |

## Install

Preview first:

```bash
uv run demiurge package install stt_openai --core assistant --preview
```

Install one provider:

```bash
uv run demiurge package install stt_openai --core assistant
```

To switch providers, uninstall the current STT package first:

```bash
uv run demiurge package uninstall stt_openai --core assistant
uv run demiurge package install stt_gemini --core assistant
```

## Common Options

Most STT packages accept:

| Option | Description |
| --- | --- |
| `api_key` or provider credential options | Direct credential value. Leave empty to use environment variables. |
| `language` | Optional spoken-language hint. |
| `include_metadata` | Adds compact provider/source metadata beside the transcript. |

Provider-specific packages may add options such as timestamp granularity,
speaker labels, diarization, inverse text normalization, region, or model id.
Use preview to inspect the exact config that will be installed.

## Credential Environment Variables

| Package | Environment variables |
| --- | --- |
| `stt_openai` | `DEMIURGE_OPENAI_API_KEY` or `OPENAI_API_KEY` |
| `stt_groq` | `DEMIURGE_GROQ_API_KEY` or `GROQ_API_KEY` |
| `stt_deepgram` | `DEMIURGE_DEEPGRAM_API_KEY` or `DEEPGRAM_API_KEY` |
| `stt_assemblyai` | `DEMIURGE_ASSEMBLYAI_API_KEY` or `ASSEMBLYAI_API_KEY` |
| `stt_gemini` | `DEMIURGE_GEMINI_API_KEY`, `GEMINI_API_KEY`, or `GOOGLE_API_KEY` |
| `stt_dashscope` | `DEMIURGE_DASHSCOPE_API_KEY` or `DASHSCOPE_API_KEY` |
| `stt_baidu` | `DEMIURGE_BAIDU_ACCESS_TOKEN`, or `DEMIURGE_BAIDU_API_KEY` plus `DEMIURGE_BAIDU_SECRET_KEY` |
| `stt_tencent` | `DEMIURGE_TENCENT_SECRET_ID` plus `DEMIURGE_TENCENT_SECRET_KEY` |

## Runtime Behavior

The installed input slot runs before `base_input`. When a turn includes a
supported audio or video attachment, the slot sends it to the provider, then
adds a transcript section to the current user prompt. The original attachment
metadata can be included when `include_metadata=true`.

If no supported attachment is present, the slot does not modify the prompt.

## Verify

List installed packages:

```bash
uv run demiurge package list --core assistant
```

Send an audio attachment through the TUI or a configured channel. The model
request should include a `Voice message transcript` section before the original
user text.
