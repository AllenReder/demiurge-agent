---
sidebar_position: 3
title: Text-to-Speech Packages
description: 安装用于 assistant audio output 的内置 text-to-speech packages。
---

# Text-to-Speech Packages

内置 text-to-speech packages 会从 assistant replies 生成音频。每个 provider 会安装一个
package-owned output slot，也可以选择安装一个 authored tool，用于按需生成语音。

除非你确实想让多个 audio providers 在每次 assistant response 后都运行，否则每个 core
只安装一个 TTS output package。

## Packages

| Package | Provider |
| --- | --- |
| `tts_minimax` | MiniMax |
| `tts_openai` | OpenAI |
| `tts_gemini` | Gemini |
| `tts_xai` | xAI |

## 安装

先 preview：

```bash
uv run demiurge package install tts_minimax --core assistant --preview
```

安装 direct output mode：

```bash
uv run demiurge package install tts_minimax --core assistant
```

安装 summary mode：

```bash
uv run demiurge package install tts_minimax \
  --core assistant \
  --option mode=summary
```

安装 on-demand TTS tool：

```bash
uv run demiurge package install tts_minimax \
  --core assistant \
  --option enable_tool=true
```

## Options

| Option | Default | 说明 |
| --- | --- | --- |
| `mode` | `direct` | `direct` 直接朗读 assistant reply。`summary` 会安装共享的 `tts_summarizer` core，并合成它生成的摘要。 |
| `enable_tool` | `false` | 安装 authored `text_to_speech` tool，用于按需生成语音。 |
| `api_key` | unset | 直接传入 provider API key。留空则使用环境变量。 |

## Credential 环境变量

| Package | 环境变量 |
| --- | --- |
| `tts_minimax` | `DEMIURGE_MINIMAX_API_KEY`；可选 `DEMIURGE_MINIMAX_GROUP_ID` |
| `tts_openai` | `DEMIURGE_OPENAI_API_KEY` 或 `OPENAI_API_KEY` |
| `tts_gemini` | `DEMIURGE_GEMINI_API_KEY`、`GEMINI_API_KEY` 或 `GOOGLE_API_KEY` |
| `tts_xai` | `DEMIURGE_XAI_API_KEY` 或 `XAI_API_KEY` |

## Runtime 行为

已安装 output slot 会在 output pipeline 中的 `base_output` 之后运行。`direct` mode 会把
assistant reply text 发送给 provider。`summary` mode 会先运行共享的
`tts_summarizer` child core，再合成摘要。

生成的音频会写入 workspace 下：

```text
.demiurge-tts/
```

Output slot 会发出 audio artifact delivery。支持 audio 的 channels 可以把这个 artifact
转发给用户。

## Tools

`enable_tool=true` 时，package 会安装一个 authored TTS tool：

| Package | Tool |
| --- | --- |
| `tts_minimax` | `text_to_speech` |
| `tts_openai` | `text_to_speech` |
| `tts_gemini` | `text_to_speech` |
| `tts_xai` | `text_to_speech` |

当 model 应该按需生成特定音频片段，而不是通过 output slot 朗读每次 assistant response
时，使用这个 tool。

## 验证

列出已安装 packages：

```bash
uv run demiurge package list --core assistant
```

运行一个 turn，然后检查 workspace：

```text
.demiurge-tts/
```

如果 `enable_tool=true`，在 TUI 中检查 tool registry：

```text
/tools
```
