---
sidebar_position: 3
title: 文字转语音包
description: 安装内置 text-to-speech packages，用于 assistant 音频输出。
---

# 文字转语音包

内置 text-to-speech packages 会从 assistant replies 生成音频。每个 provider 都会安装一个 output slot，并且可以选择安装一个 authored `text_to_speech` tool，用于按需生成。

除非你有意让多个音频 providers 在每次 assistant response 后运行，否则每个 core 只安装一个 TTS output package。

## 包

| 包 | Provider | 能力 |
| --- | --- | --- |
| `tts_minimax` | MiniMax | `network.fetch`, `agents.run:tts_summarizer` |
| `tts_openai` | OpenAI | `network.fetch`, `agents.run:tts_summarizer` |
| `tts_gemini` | Gemini | `network.fetch`, `agents.run:tts_summarizer` |
| `tts_xai` | xAI | `network.fetch`, `agents.run:tts_summarizer` |

## 安装

使用交互式 manager：

```bash
uv run demiurge package
```

或者用 subcommands 预览并安装：

```bash
uv run demiurge package install tts_minimax --core assistant --preview
uv run demiurge package install tts_minimax --core assistant
```

安装 summary mode：

```bash
uv run demiurge package install tts_minimax \
  --core assistant \
  --option mode=summary
```

安装 on-demand tool：

```bash
uv run demiurge package install tts_minimax \
  --core assistant \
  --option enable_tool=true
```

## 共享选项

所有内置 TTS packages 都支持：

| 选项 | 默认值 | 说明 |
| --- | --- | --- |
| `mode` | `direct` | `direct` 原样朗读 assistant reply。`summary` 安装 shared `tts_summarizer` child core，并朗读它的 summary。可选值：`direct`、`summary`。 |
| `enable_tool` | `false` | 安装 authored `text_to_speech` tool 和 provider-specific TTS skill。 |
| `api_key` | unset | 可选 direct provider API key。留空则使用环境变量。 |

在 `summary` mode 下，package 会安装或复用名为 `tts_summarizer` 的 package-owned child core。

## 凭证环境变量

| 包 | 环境变量 |
| --- | --- |
| `tts_minimax` | `DEMIURGE_MINIMAX_API_KEY`; optional `DEMIURGE_MINIMAX_GROUP_ID` |
| `tts_openai` | `DEMIURGE_OPENAI_API_KEY` or `OPENAI_API_KEY` |
| `tts_gemini` | `DEMIURGE_GEMINI_API_KEY`, `GEMINI_API_KEY`, or `GOOGLE_API_KEY` |
| `tts_xai` | `DEMIURGE_XAI_API_KEY` or `XAI_API_KEY` |

## 运行时行为

已安装 output slot 会在 model response 可用后，在 parallel output pipeline 中运行。在 `direct` mode 下，它会从 assistant reply 中去除常见 Markdown，将文本截断到已配置 provider limit，发送给 provider，并发出 audio artifact。

在 `summary` mode 下，它会先调用 `tts_summarizer` child core，然后合成返回的 summary。

生成的音频写入 workspace 下：

```text
.demiurge-tts/
```

支持音频的 channels 可以把 audio artifact 转发给用户。

## 工具

当 `enable_tool=true` 时，package 会安装：

```text
agent/tools/text_to_speech/
```

Model 可以使用该 tool 按需生成特定 audio clip，而不是通过 output slot 朗读每次 assistant response。

因为所有 provider packages 都 target `agent/tools/text_to_speech`，同一个 core 中一次只安装一个启用 tool 的 TTS provider。

## 验证

列出已安装 packages：

```bash
uv run demiurge package list --core assistant
```

运行一个 turn，并检查 workspace：

```text
.demiurge-tts/
```

如果 `enable_tool=true`，在 TUI 中检查 tool registry：

```text
/tools
```

## 卸载

```bash
uv run demiurge package uninstall tts_minimax --core assistant --preview
uv run demiurge package uninstall tts_minimax --core assistant
```

Uninstall 会移除 package-owned output slots、provider libs、optional tool 和 skill files，以及 package-owned pipeline entries。它不会移除 `.demiurge-tts/` 下生成的音频。
