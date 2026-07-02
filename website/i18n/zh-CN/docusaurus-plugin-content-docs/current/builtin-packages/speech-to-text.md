---
sidebar_position: 2
title: 语音转文字包
description: 安装内置 speech-to-text packages，用于转录音频和视频附件。
---

# 语音转文字包

内置 speech-to-text packages 会在 model request 前转录支持的音频或视频附件。每个 provider package 都会在 `agent/input/speech_to_text` 安装一个 input slot，因此同一个 core 中一次只安装一个 STT provider package。

## 包

| 包 | Provider | 能力 |
| --- | --- | --- |
| `stt_openai` | OpenAI | `network.fetch` |
| `stt_groq` | Groq | `network.fetch` |
| `stt_deepgram` | Deepgram | `network.fetch` |
| `stt_assemblyai` | AssemblyAI | `network.fetch` |
| `stt_gemini` | Gemini | `network.fetch` |
| `stt_dashscope` | Alibaba Cloud Model Studio | `network.fetch` |
| `stt_baidu` | Baidu Cloud Speech Recognition | `network.fetch` |
| `stt_tencent` | Tencent Cloud ASR | `network.fetch` |

## 安装

使用交互式 manager：

```bash
uv run demiurge package
```

或者用 subcommands 预览并安装：

```bash
uv run demiurge package install stt_openai --core assistant --preview
uv run demiurge package install stt_openai --core assistant
```

切换 providers 时，先卸载当前 STT package：

```bash
uv run demiurge package uninstall stt_openai --core assistant
uv run demiurge package install stt_gemini --core assistant
```

## 共享行为

所有 STT packages 都会安装：

```text
agent/input/speech_to_text/
agent/lib/stt_common/
agent/lib/<provider>/
```

Input slot 会追加到 serial input pipeline。当 turn 带有支持的音频或视频附件时，该 slot 需要 `network.fetch`，会把附件发送给 provider，并把 `Voice message transcript` section 添加到当前 user prompt。如果没有支持的附件，它不会执行任何操作。

支持的 media 包括 AAC、FLAC、M4A、MP3、OGG、OPUS、WAV 和 WebM 等常见音频格式，以及 provider 接受的 MP4/WebM 视频附件。

## 选项

| 包 | 选项 |
| --- | --- |
| `stt_openai` | `api_key`, `language`, `context_hint`, `timestamp_granularity`, `include_metadata` |
| `stt_groq` | `api_key`, `language`, `context_hint`, `timestamp_granularity`, `include_metadata` |
| `stt_deepgram` | `api_key`, `language`, `detect_language`, `diarization`, `include_metadata` |
| `stt_assemblyai` | `api_key`, `language`, `detect_language`, `speaker_labels`, `include_metadata` |
| `stt_gemini` | `api_key`, `language`, `include_timestamps`, `include_metadata` |
| `stt_dashscope` | `api_key`, `language`, `include_metadata`, `enable_itn` |
| `stt_baidu` | `api_key`, `secret_key`, `access_token`, `language`, `dev_pid`, `include_metadata` |
| `stt_tencent` | `secret_id`, `secret_key`, `region`, `engine_model_type`, `include_metadata` |

`timestamp_granularity` 的可选值是 `none`、`segment` 和 `word`。

可选 secret options 可以在安装期间传入，也可以留空，让已安装 provider config 在运行时读取环境变量。

## 凭证环境变量

| 包 | 环境变量 |
| --- | --- |
| `stt_openai` | `DEMIURGE_OPENAI_API_KEY` or `OPENAI_API_KEY` |
| `stt_groq` | `DEMIURGE_GROQ_API_KEY` or `GROQ_API_KEY` |
| `stt_deepgram` | `DEMIURGE_DEEPGRAM_API_KEY` or `DEEPGRAM_API_KEY` |
| `stt_assemblyai` | `DEMIURGE_ASSEMBLYAI_API_KEY` or `ASSEMBLYAI_API_KEY` |
| `stt_gemini` | `DEMIURGE_GEMINI_API_KEY`, `GEMINI_API_KEY`, or `GOOGLE_API_KEY` |
| `stt_dashscope` | `DEMIURGE_DASHSCOPE_API_KEY` or `DASHSCOPE_API_KEY` |
| `stt_baidu` | `DEMIURGE_BAIDU_ACCESS_TOKEN`, or `DEMIURGE_BAIDU_API_KEY` plus `DEMIURGE_BAIDU_SECRET_KEY` |
| `stt_tencent` | `DEMIURGE_TENCENT_SECRET_ID` plus `DEMIURGE_TENCENT_SECRET_KEY` |

## 验证

列出已安装 packages：

```bash
uv run demiurge package list --core assistant
```

通过 TUI 或已配置 channel 发送音频附件。Model request 应该在原始 user text 之前或旁边包含 `Voice message transcript` section。

## 卸载

```bash
uv run demiurge package uninstall stt_openai --core assistant --preview
uv run demiurge package uninstall stt_openai --core assistant
```

Uninstall 会移除 package-owned input slot 和 provider lib。如果另一个已安装 package 仍引用 shared `agent/lib/stt_common/`，它会被保留。
