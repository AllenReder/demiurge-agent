---
title: 编写 Package Recipe
description: 设计带有 options、components、pipeline placement、config overlays 和 conditions 的 packages/<package_id>.yaml。
---

# 编写 Package Recipe

当你已经知道要打包的行为，并且需要编写 `packages/<package_id>.yaml` 时，使用本指南。

Package recipe 告诉 Demiurge 要把哪些 authored-surface files 安装进 runtime Agent Core。它不定义 runtime code 本身。Slot modules、tool modules、skills、libraries、child cores、MCP manifests、schedules 和默认 `config.yaml` 文件位于 component source directories 中。

完整字段词典见 [Package Recipe 参考](../reference/package-recipes.md)。如果你还没有 repository root，请先从 [创建外部 Package Repository](../tutorials/external-package-repository.md) 开始。

## 1. 选择能力形状

从要安装的行为出发，选择 component kind：

| 目标 | Component kind |
| --- | --- |
| 在 session 或 turn 前添加 context | `bootstrap` |
| 在 model request 前转换用户输入 | `input` |
| 在 model response 后处理 assistant output | `output` |
| 添加 model-callable authored tool | `tool` |
| 添加可复用作者指导 | `skill` |
| 添加 package-owned helper code 或 config | `lib` |
| 添加 package-owned child Agent Core | `core` |
| 添加 MCP server declaration | `mcp` |
| 添加 schedule declaration | `schedule` |

当 slot modules 和 tools 需要共享代码时，用 `lib`。只有 package 需要独立 runtime Agent Core 时才用 `core`，例如 summarizer。

## 2. 从 Package Identity 开始

在下面路径创建一个 recipe：

```text
packages/<package_id>.yaml
```

先写稳定的 identity fields：

```yaml
schema_version: 1
id: reply_style
name: Reply Style
summary: Add a package-provided reply style hint.
tags:
  - input
  - style
capabilities: []
manual_dependencies: []
components: []
```

使用稳定的 `id`。已安装 runtime core 会在 `packages.yaml` 中记录它，带 repository 前缀的 refs 也使用它：

```text
community/reply_style
```

只在需要人工 review 的 warning 中使用 `manual_dependencies`。Demiurge 不会安装 Python dependencies，也不会编辑 `uv.lock`。

## 3. 设计安装选项

只有需要在安装时选择的值才放进 `options`。固定行为应该留在 component source files 或默认 `config.yaml` 中。

```yaml
options:
  - id: api_key
    type: secret
    prompt: Provider API key
    description: Optional direct API key; leave empty to read PROVIDER_API_KEY.
    required: false
    default: null
  - id: mode
    type: choice
    prompt: Runtime mode
    description: Choose whether to install only direct output or also a child core.
    default: direct
    choices:
      - value: direct
        description: Use the assistant reply as-is.
      - value: summary
        description: Summarize the assistant reply before output.
  - id: enable_tool
    type: bool
    prompt: Add authored tool
    description: Also install a model-callable tool.
    default: false
```

支持的 option types 是 `string`、`bool`、`choice`、`path` 和 `secret`。`secret` values 可能会写入已安装 component config，但 install record 只保存 `<redacted>`。

在 component `config` 或 `config_when` 中使用 `${options.<id>}` 渲染已解析 option values。

## 4. 添加 Components

每个 component entry 都指向对应 repository root 下的 source：

```yaml
components:
  - id: reply_style_input
    kind: input
    source: reply_style
    target: agent/input/reply_style
    pipeline:
      group: serial
      append: true
```

这会从下面路径安装文件：

```text
input/reply_style/
```

安装到：

```text
agent/input/reply_style/
```

`bootstrap`、`input` 和 `output` components 需要 `slot.yaml` 和 `pipeline` entry。`tool` components 需要 `tool.yaml`。`lib` 和 `skill` components 会作为目录复制。`core` components 会创建 package-owned runtime core。`mcp` 和 `schedule` components 会安装一个 YAML declaration file。

## 5. 添加 Config Overlays

当 package recipe 需要用 install options patch component 默认 `config.yaml` 时，使用 `config`。

Component source 必须包含 `config.yaml`：

```text
lib/web_search_brave/
  config.yaml
  provider.py
```

Recipe 可以把 option 渲染进该 config：

```yaml
components:
  - id: web_search_lib
    kind: lib
    source: web_search_brave
    target: agent/lib/web_search_brave
    config:
      api_key: ${options.api_key}
```

精确 option reference 会保留已解析 value type。例如 `${options.enable_tool}` 作为完整 value 使用时仍是 boolean。嵌入在更长字符串中的 option reference 会渲染为 text。

当 component 总是安装、但只有某些 mode 需要额外 config 时，使用 `config_when`：

```yaml
components:
  - id: tts_output
    kind: output
    source: tts_minimax
    target: agent/output/tts_minimax
    pipeline:
      group: parallel
      append: true
    config_when:
      - when:
          mode: summary
        config:
          summarizer_core: tts_summarizer
          summary: MiniMax summarized TTS audio
```

`config` 和 `config_when` 对 `core` components 无效。

## 6. Gate Optional Components

当 component 只应在某些 option values 下安装时，使用 `when`：

```yaml
components:
  - id: tts_tool
    kind: tool
    source: text_to_speech_minimax
    target: agent/tools/text_to_speech
    when:
      enable_tool: true
  - id: tts_summarizer
    kind: core
    source: tts_summarizer
    target_core_id: tts_summarizer
    when:
      mode: summary
  - id: tts_voice_skill
    kind: skill
    source: tts_voice
    target: agent/skills/tts_voice
    when:
      enable_tool: true
```

Conditions 会精确匹配 resolved option values。`when` 或 `config_when.when` 中使用的每个 option id 都必须在 `options` 中声明。

## 7. 常见 Recipe 模式

### 简单 Input Slot

当 package 只添加一个 input transformation 时，使用这种结构：

```yaml
schema_version: 1
id: reply_style
name: Reply Style
summary: Add a package-provided reply style hint.
tags:
  - input
  - style
capabilities: []
components:
  - id: reply_style_input
    kind: input
    source: reply_style
    target: agent/input/reply_style
    pipeline:
      group: serial
      append: true
```

### 带 Secret Config 的 Provider Package

当 package 需要 provider credentials 和 shared code 时，使用这种结构：

```yaml
schema_version: 1
id: web_search_brave
name: Brave Web Search
summary: Search the web with Brave Search through a package-owned web_search tool.
tags:
  - web
  - search
  - provider:brave
options:
  - id: api_key
    type: secret
    prompt: Brave Search API key
    description: Optional direct Brave Search API key; leave empty to read DEMIURGE_BRAVE_SEARCH_API_KEY.
    required: false
    default: null
capabilities:
  - network.fetch
components:
  - id: web_search_tool
    kind: tool
    source: web_search_brave
    target: agent/tools/web_search
  - id: web_search_lib
    kind: lib
    source: web_search_brave
    target: agent/lib/web_search_brave
    config:
      api_key: ${options.api_key}
```

### 可选 Child Core 和 Tool

当一个 mode 会安装额外 authored surface 时，使用这种结构：

```yaml
schema_version: 1
id: tts_minimax
name: MiniMax TTS
summary: Generate speech audio with MiniMax, either directly or through a summarizer core.
tags:
  - audio
  - tts
  - provider:minimax
options:
  - id: mode
    type: choice
    prompt: TTS mode
    description: Choose whether the output module speaks the final text directly or first runs a summarizer core.
    default: direct
    choices:
      - value: direct
        description: Generate speech from the assistant reply as-is.
      - value: summary
        description: Summarize the assistant reply before generating speech.
  - id: enable_tool
    type: bool
    prompt: Add agent TTS tool
    description: Also install an authored tool so the agent can generate speech on demand.
    default: false
components:
  - id: tts_output
    kind: output
    source: tts_minimax
    target: agent/output/tts_minimax
    pipeline:
      group: parallel
      append: true
    config_when:
      - when:
          mode: summary
        config:
          summarizer_core: tts_summarizer
  - id: tts_tool
    kind: tool
    source: text_to_speech_minimax
    target: agent/tools/text_to_speech
    when:
      enable_tool: true
  - id: tts_summarizer
    kind: core
    source: tts_summarizer
    target_core_id: tts_summarizer
    when:
      mode: summary
```

## 8. 验证 Recipe

验证 repository 和 recipe 能加载：

```bash
uv run demiurge package repo add ~/demiurge-packages --alias local --trust
uv run demiurge package list --repo local
```

写入 runtime core 前先预览：

```bash
uv run demiurge package install local/reply_style --core assistant --preview
```

如果 package 安装了 slot、tool、MCP declaration、schedule declaration 或 child core，检查目标 runtime 仍能加载：

```bash
uv run demiurge init --check
```

当 recipe 准备好并且你想共享它时，继续阅读 [发布 Package Repository](publish-package-repository.md)。
