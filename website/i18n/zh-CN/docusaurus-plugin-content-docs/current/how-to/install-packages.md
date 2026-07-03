---
title: 安装包
description: 先使用交互式 package manager；需要时再用脚本列出、安装和卸载 package。
---

# 安装包

当现有 Agent Core 需要可选能力时使用 package，例如 memory、speech-to-text、text-to-speech、web search、style hints、MCP declarations 或 schedules。

常规入口是交互式 package manager：

```bash
uv run demiurge package
```

它可以选择 runtime core、浏览 package、按 repository 或 tag 过滤、预览变更、安装 package、卸载已安装 package，以及管理 package repositories。Preview 和 list views 是 read-only；install 和 uninstall 会作为 Git transaction 提交 live agents tree。

当你需要在脚本、runbook 或 issue comment 中使用可重复执行的命令时，再使用本页的 subcommands。

## 安装前准备

从已初始化的 runtime core 开始：

```bash
uv run demiurge init
```

列出可用 package：

```bash
uv run demiurge package list --core assistant
```

按 repository 或 tag 过滤：

```bash
uv run demiurge package list --repo builtin
uv run demiurge package list --tag memory
uv run demiurge package list --tag stt
```

如果两个 repositories 包含相同的 package id，使用带 repository 前缀的 ref：

```bash
builtin/memory_basic
```

如果你直接编辑过 `~/.demiurge/agents`，package install 和 uninstall 会先把这些 local
agent edits 保存为独立 core revision。然后 package operation 会在自己的 Git transaction
中继续执行。安装前可用 `uv run demiurge core diff` 检查尚未保存的 edits。

## 预览安装

先预览。预览会显示哪些 targets 将被写入或复用，以及哪些 manual warnings 适用。

```bash
uv run demiurge package install memory_basic --core assistant --preview
```

Provider packages 通常需要凭证。用重复的 `--option` flags 传入 package options，或者保留可选 secret 为空，让已安装 component 在运行时读取文档说明的环境变量：

```bash
uv run demiurge package install tts_minimax \
  --core assistant \
  --option mode=summary \
  --option enable_tool=true \
  --preview
```

## 安装包

预览结果正确后再安装：

```bash
uv run demiurge package install memory_basic --core assistant
```

需要时安装带 repository 前缀的 package：

```bash
uv run demiurge package install builtin/memory_basic --core assistant
```

Install options 只在安装期间解析一次。Secret option values 可能会写入已安装 component config，但 `packages.yaml` 只保存已脱敏的 option snapshots。成功安装会运行 host-owned gates，并创建新的 core revision。如果安装前存在 local agent edits，Demiurge 会先把这些 edits 保存为单独 revision，然后再报告 package revision。

## 安装会写入什么

Package installation 会写入 active runtime core，而不是 source template checkout。对于默认 core，位置在：

```text
~/.demiurge/agents/assistant/
```

Installation 可以把 package-owned components 复制到：

```text
agent/bootstrap/
agent/input/
agent/output/
agent/tools/
agent/skills/
agent/lib/
```

它还可以创建 package-owned child cores、MCP declaration YAML files 和 schedule declaration YAML files。

当 package 安装 `bootstrap`、`input` 或 `output` slot 时，Demiurge 也会更新目标 core 的 `agent/pipelines.yaml`。Bootstrap slots 始终位于 serial bootstrap pipeline。Input 和 output slots 可以是 serial 或 parallel，取决于 package recipe。

安装记录写入：

```text
~/.demiurge/agents/<core-id>/packages.yaml
```

Packages 不会安装 Python dependencies，也不会编辑 `uv.lock`。`manual_dependencies` 是给人工 dependency review 的 warnings。

`packages.yaml` 是 provenance，不是 runtime truth。它记录 installed targets 和 hashes，
供 package list/review 报告 drift、供 uninstall 判断是否安全移除。

## 内置包家族

内置 repository 目前提供：

| Family | Packages |
| --- | --- |
| Memory | `memory_basic`, `memory_honcho` |
| Context | `context_reseed` |
| Communication | `conversation_style` |
| Web search | `web_search_brave`, `web_search_tavily` |
| Speech-to-text | `stt_openai`, `stt_groq`, `stt_deepgram`, `stt_assemblyai`, `stt_gemini`, `stt_dashscope`, `stt_baidu`, `stt_tencent` |
| Text-to-speech | `tts_minimax`, `tts_openai`, `tts_gemini`, `tts_xai` |

查看内置 package 页面，了解 package 行为和 options：

- [memory_basic](../builtin-packages/memory/memory_basic.md)
- [memory_honcho](../builtin-packages/memory/memory_honcho.md)
- [context_reseed](../builtin-packages/context-reseed.md)
- [conversation_style](../builtin-packages/conversation-style.md)
- [Web Search Packages](../builtin-packages/web-search.md)
- [Speech-to-Text Packages](../builtin-packages/speech-to-text.md)
- [Text-to-Speech Packages](../builtin-packages/text-to-speech.md)

## 切换 Provider 包

有些 provider packages 会有意共享相同 target。例如，所有 STT packages 都 target `agent/input/speech_to_text`，两个 web search packages 都 target `agent/tools/web_search`。

安装另一个 provider package 前，先卸载当前 provider package：

```bash
uv run demiurge package uninstall web_search_brave --core assistant --preview
uv run demiurge package uninstall web_search_brave --core assistant
uv run demiurge package install web_search_tavily --core assistant --preview
uv run demiurge package install web_search_tavily --core assistant
```

STT packages 也使用相同模式：

```bash
uv run demiurge package uninstall stt_openai --core assistant
uv run demiurge package install stt_gemini --core assistant
```

## 卸载包

预览移除：

```bash
uv run demiurge package uninstall memory_basic --core assistant --preview
```

卸载：

```bash
uv run demiurge package uninstall memory_basic --core assistant
```

Uninstall 会移除 package-owned component targets，移除 `bootstrap`、`input` 和 `output` slots 的 package-owned pipeline entries，并更新 `packages.yaml`。成功 uninstall 也会提交 runtime agents tree。

和 install 一样，uninstall 会先把无关 local agent edits 保存为独立 revision，不会把手动 edits 混进 package uninstall commit。

如果 package-owned files 已经 drift，uninstall 会拒绝移除。明确要破坏性移除时使用：

```bash
uv run demiurge package uninstall memory_basic --core assistant --force-drift
```

Uninstall 不会移除写在 package-owned targets 之外的数据。例如 memory data、generated audio、context notes、caches 和 provider outbox files 会保留，除非你自行删除。

## 验证

列出该 core 已安装的 packages：

```bash
uv run demiurge package list --core assistant
```

检查 runtime core 仍能加载：

```bash
uv run demiurge core check
```

运行一个 fake-provider turn：

```bash
uv run demiurge --provider fake
```

如果 package 安装了 tools，在 TUI 中检查 tool list：

```text
/tools
```

## 管理仓库

用交互式 manager 处理 repository operations：

```bash
uv run demiurge package
```

需要脚本化 repository commands 时，参见 [管理 Package Repositories](manage-package-repositories.md)。

如果你正在创建供其他用户安装的 repository，请阅读 [发布 Package Repository](publish-package-repository.md)。

## 边界

Package management 是用户控制的 CLI workflow。它不是 agent-callable model tool。

Packages 安装 authored-surface files。Host 仍然拥有 sessions、provider calls、approvals、MCP transport、schedule execution 和 dependency policy。
