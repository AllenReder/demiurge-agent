---
sidebar_position: 2
title: Speech-to-Text Packages
description: Install built-in speech-to-text packages for audio and video attachment transcription.
---

# Speech-to-Text Packages

Built-in speech-to-text packages transcribe supported audio or video
attachments before the model request. Each provider package installs an input
slot at `agent/input/speech_to_text`, so install only one STT provider package
in a core at a time.

## Packages

| Package | Provider | Capability |
| --- | --- | --- |
| `stt_openai` | OpenAI | `network.fetch` |
| `stt_groq` | Groq | `network.fetch` |
| `stt_deepgram` | Deepgram | `network.fetch` |
| `stt_assemblyai` | AssemblyAI | `network.fetch` |
| `stt_gemini` | Gemini | `network.fetch` |
| `stt_dashscope` | Alibaba Cloud Model Studio | `network.fetch` |
| `stt_baidu` | Baidu Cloud Speech Recognition | `network.fetch` |
| `stt_tencent` | Tencent Cloud ASR | `network.fetch` |

## Install

Use the interactive manager:

```bash
uv run demiurge package
```

Or preview and install with subcommands:

```bash
uv run demiurge package install stt_openai --core assistant --preview
uv run demiurge package install stt_openai --core assistant
```

To switch providers, uninstall the current STT package first:

```bash
uv run demiurge package uninstall stt_openai --core assistant
uv run demiurge package install stt_gemini --core assistant
```

## Shared Behavior

All STT packages install:

```text
agent/input/speech_to_text/
agent/lib/stt_common/
agent/lib/<provider>/
```

The input slot appends to the serial input pipeline. When a turn has a supported
audio or video attachment, the slot requires `network.fetch`, sends the
attachment to the provider, and adds a `Voice message transcript` section to the
current user prompt. If no supported attachment is present, it does nothing.

Supported media includes common audio formats such as AAC, FLAC, M4A, MP3, OGG,
OPUS, WAV, and WebM, plus MP4/WebM video attachments when the provider accepts
them.

## Options

| Package | Options |
| --- | --- |
| `stt_openai` | `api_key`, `language`, `context_hint`, `timestamp_granularity`, `include_metadata` |
| `stt_groq` | `api_key`, `language`, `context_hint`, `timestamp_granularity`, `include_metadata` |
| `stt_deepgram` | `api_key`, `language`, `detect_language`, `diarization`, `include_metadata` |
| `stt_assemblyai` | `api_key`, `language`, `detect_language`, `speaker_labels`, `include_metadata` |
| `stt_gemini` | `api_key`, `language`, `include_timestamps`, `include_metadata` |
| `stt_dashscope` | `api_key`, `language`, `include_metadata`, `enable_itn` |
| `stt_baidu` | `api_key`, `secret_key`, `access_token`, `language`, `dev_pid`, `include_metadata` |
| `stt_tencent` | `secret_id`, `secret_key`, `region`, `engine_model_type`, `include_metadata` |

`timestamp_granularity` choices are `none`, `segment`, and `word`.

Optional secret options can be passed during installation or left empty so the
installed provider config reads environment variables at runtime.

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

## Verify

List installed packages:

```bash
uv run demiurge package list --core assistant
```

Send an audio attachment through the TUI or a configured channel. The model
request should include a `Voice message transcript` section before or beside the
original user text.

## Uninstall

```bash
uv run demiurge package uninstall stt_openai --core assistant --preview
uv run demiurge package uninstall stt_openai --core assistant
```

Uninstall removes the package-owned input slot and provider lib. Shared
`agent/lib/stt_common/` is kept if another installed package still references it.
