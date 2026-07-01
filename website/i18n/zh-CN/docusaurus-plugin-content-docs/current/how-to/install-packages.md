---
title: 安装 Packages
description: Preview、安装、列出和卸载可复用 Agent Core packages。
---

# 安装 Packages

Packages 会把可复用 components 安装进 runtime Agent Core。它们可以安装 Agent
Slots、tools、skills、libraries 和 child cores。

## 使用交互式 Package Manager

简单安装和管理 package 时，直接启动交互式 package manager：

```bash
uv run demiurge package
```

用这个流程可以浏览 packages、为 runtime core 安装或卸载 packages，以及管理
package repositories，不需要记住下面每个独立 subcommand。

## 列出 Packages

```bash
uv run demiurge package list --core assistant
uv run demiurge package list --repo builtin
```

## Preview 安装

```bash
uv run demiurge package install memory_basic --core assistant --preview
```

安装会添加 Agent Slots、tools 或外部 provider integration 的 package 前，先使用
preview。

## 安装

```bash
uv run demiurge package install memory_basic --core assistant
```

Package 名称有歧义时，使用 repository-qualified package id：

```bash
uv run demiurge package install builtin/memory_basic --core assistant
```

用重复的 `--option` flags 传入 options：

```bash
uv run demiurge package install minimax_tts \
  --core assistant \
  --option mode=summary \
  --option enable_tool=true
```

Provider-owned web search packages 会暴露同一个 model-facing tool name：
`web_search`。

```bash
uv run demiurge package install web_search_brave --core assistant --preview
uv run demiurge package install web_search_tavily --core assistant --preview
```

因为两个 packages 都 target `agent/tools/web_search`，同一个 core 中一次只安装一个
web search provider package。要切换 provider，先卸载当前 web search package。

Provider-owned speech-to-text packages 会在 model request 前转录音频附件：

```bash
uv run demiurge package list --tag stt
uv run demiurge package install stt_dashscope --core assistant --preview
```

内置 STT packages 包括 `stt_openai`、`stt_groq`、`stt_deepgram`、
`stt_assemblyai`、`stt_gemini`、`stt_dashscope`、`stt_baidu` 和
`stt_tencent`。它们都 target `agent/input/speech_to_text`，所以同一个 core 中一次只安装一个
STT provider package。要切换 provider，先卸载当前 STT package。

常用凭证环境变量：

| Package | 环境变量 |
| --- | --- |
| `stt_dashscope` | `DEMIURGE_DASHSCOPE_API_KEY` 或 `DASHSCOPE_API_KEY` |
| `stt_baidu` | `DEMIURGE_BAIDU_ACCESS_TOKEN`，或 `DEMIURGE_BAIDU_API_KEY` 加 `DEMIURGE_BAIDU_SECRET_KEY` |
| `stt_tencent` | `DEMIURGE_TENCENT_SECRET_ID` 加 `DEMIURGE_TENCENT_SECRET_KEY` |

## 卸载

```bash
uv run demiurge package uninstall memory_basic --core assistant --preview
uv run demiurge package uninstall memory_basic --core assistant
```

Uninstall 会移除 package-owned component targets，并更新 `packages.yaml`。它不会
移除写在 owned targets 之外的 package data。

## 添加外部 Repository

```bash
uv run demiurge package repo add https://github.com/user/demiurge-packages.git \
  --alias community \
  --ref main \
  --trust
```

本地 repository：

```bash
uv run demiurge package repo add ./local-packages --alias local --trust
```

Trust 必须显式授予，因为 repositories 可以安装可执行的本地 code。

## 验证

```bash
uv run demiurge package list --core assistant
uv run demiurge init --check
uv run demiurge --provider fake
```

如果 package 安装了 tool，检查可见 tool registry：

```text
/tools
```

## 边界

Package management 是用户控制的 CLI workflow。它不是 agent-callable model tool。
Package recipes 不会安装 Python dependencies，也不会编辑 host `uv.lock`。
