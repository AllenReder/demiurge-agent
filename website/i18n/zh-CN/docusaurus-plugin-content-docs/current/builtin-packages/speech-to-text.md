---
sidebar_position: 2
title: Speech-to-Text Packages
description: 安装用于音频附件转录的内置 speech-to-text packages。
---

# Speech-to-Text Packages

内置 speech-to-text packages 会在 model request 前转录音频或视频附件。每个 STT
provider package 都把 provider module 安装到共享的
`agent/input/speech_to_text` target，所以同一个 core 中一次只安装一个 STT
provider package。

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

## 安装

先 preview：

```bash
uv run demiurge package install stt_openai --core assistant --preview
```

安装一个 provider：

```bash
uv run demiurge package install stt_openai --core assistant
```

切换 provider 时，先卸载当前 STT package：

```bash
uv run demiurge package uninstall stt_openai --core assistant
uv run demiurge package install stt_gemini --core assistant
```

## Common Options

大多数 STT packages 接受：

| Option | 说明 |
| --- | --- |
| `api_key` 或 provider credential options | 直接传入 credential value。留空则使用环境变量。 |
| `language` | 可选 spoken-language hint。 |
| `include_metadata` | 在 transcript 旁边加入紧凑的 provider/source metadata。 |

Provider-specific packages 可能增加 context hints、timestamp granularity、
speaker labels、diarization、inverse text normalization、region 或 model id 等
options。用 preview 检查将要安装的准确 config。

## Credential 环境变量

| Package | 环境变量 |
| --- | --- |
| `stt_openai` | `DEMIURGE_OPENAI_API_KEY` 或 `OPENAI_API_KEY` |
| `stt_groq` | `DEMIURGE_GROQ_API_KEY` 或 `GROQ_API_KEY` |
| `stt_deepgram` | `DEMIURGE_DEEPGRAM_API_KEY` 或 `DEEPGRAM_API_KEY` |
| `stt_assemblyai` | `DEMIURGE_ASSEMBLYAI_API_KEY` 或 `ASSEMBLYAI_API_KEY` |
| `stt_gemini` | `DEMIURGE_GEMINI_API_KEY`、`GEMINI_API_KEY` 或 `GOOGLE_API_KEY` |
| `stt_dashscope` | `DEMIURGE_DASHSCOPE_API_KEY` 或 `DASHSCOPE_API_KEY` |
| `stt_baidu` | `DEMIURGE_BAIDU_ACCESS_TOKEN`，或 `DEMIURGE_BAIDU_API_KEY` 加 `DEMIURGE_BAIDU_SECRET_KEY` |
| `stt_tencent` | `DEMIURGE_TENCENT_SECRET_ID` 加 `DEMIURGE_TENCENT_SECRET_KEY` |

## Runtime 行为

已安装 input slot 会在 `base_input` 前运行。Turn 包含支持的音频或视频附件时，
slot 会要求 `network.fetch`，把附件发送给 provider，然后向当前 user prompt 添加
transcript section。`include_metadata=true` 时可以包含原始附件 metadata。

如果没有支持的附件，slot 不会修改 prompt。

## 验证

列出已安装 packages：

```bash
uv run demiurge package list --core assistant
```

通过 TUI 或已配置 channel 发送音频附件。Model request 应该在原始 user text 前包含
`Voice message transcript` section。
