---
title: Install Packages
description: Preview, install, list, and uninstall reusable Agent Core packages.
---

# Install Packages

Packages install reusable components into runtime Agent Cores. They can install
Agent Slots, tools, skills, libraries, child cores, MCP declarations, and
schedule declarations.

## Use the Interactive Package Manager

For simple package installation and management, start the interactive package
manager:

```bash
uv run demiurge package
```

Use this flow to browse packages, install or uninstall packages for a runtime
core, and manage package repositories without memorizing the individual
subcommands below.

## List Packages

```bash
uv run demiurge package list --core assistant
uv run demiurge package list --repo builtin
```

## Preview an Install

```bash
uv run demiurge package install memory_basic --core assistant --preview
```

Use preview before installing packages that add Agent Slots, tools, or external
provider integration.

Packages can also install package-owned MCP server declarations and schedule
declarations. The installed YAML files are owned by the package for uninstall,
but the host still owns MCP transport, approvals, schedule claims, and schedule
execution.

## Install

```bash
uv run demiurge package install memory_basic --core assistant
```

For Honcho-backed memory, preview the package and review the manual SDK
dependency warning before installing:

```bash
uv run demiurge package install memory_honcho --core assistant --preview
uv run demiurge package install memory_honcho --core assistant
```

`memory_honcho` does not edit `uv.lock` or install `honcho-ai` for you. Install
that dependency according to your host environment policy, then set
`HONCHO_API_KEY` or pass a secret `api_key` option during package installation.
The package installs automatic recall, turn sync, and `honcho_*` tools by
default. Uninstall removes package-owned slots, tools, skill, and lib files, but
leaves `memory/honcho/` cache and outbox data in place.

Use a repository-qualified package id when package names are ambiguous:

```bash
uv run demiurge package install builtin/memory_basic --core assistant
```

Pass options with repeated `--option` flags:

```bash
uv run demiurge package install tts_minimax \
  --core assistant \
  --option mode=summary \
  --option enable_tool=true
```

Provider-owned web search packages expose the same model-facing tool name,
`web_search`:

```bash
uv run demiurge package install web_search_brave --core assistant --preview
uv run demiurge package install web_search_tavily --core assistant --preview
```

Because both packages target `agent/tools/web_search`, install only one web
search provider package in a core at a time. To switch providers, uninstall the
current web search package first.

Provider-owned speech-to-text packages transcribe audio attachments before the
model request:

```bash
uv run demiurge package list --tag stt
uv run demiurge package install stt_dashscope --core assistant --preview
```

The built-in STT packages are `stt_openai`, `stt_groq`, `stt_deepgram`,
`stt_assemblyai`, `stt_gemini`, `stt_dashscope`, `stt_baidu`, and
`stt_tencent`. They all target `agent/input/speech_to_text`, so install only one
STT provider package in a core at a time. To switch providers, uninstall the
current STT package first.

Common credential environment variables:

| Package | Environment variables |
| --- | --- |
| `stt_dashscope` | `DEMIURGE_DASHSCOPE_API_KEY` or `DASHSCOPE_API_KEY` |
| `stt_baidu` | `DEMIURGE_BAIDU_ACCESS_TOKEN`, or `DEMIURGE_BAIDU_API_KEY` plus `DEMIURGE_BAIDU_SECRET_KEY` |
| `stt_tencent` | `DEMIURGE_TENCENT_SECRET_ID` plus `DEMIURGE_TENCENT_SECRET_KEY` |

## Uninstall

```bash
uv run demiurge package uninstall memory_basic --core assistant --preview
uv run demiurge package uninstall memory_basic --core assistant
```

Uninstall removes package-owned component targets and updates `packages.yaml`.
It does not remove package data written outside owned targets.

## Add an External Repository

```bash
uv run demiurge package repo add https://github.com/user/demiurge-packages.git \
  --alias community \
  --ref main \
  --trust
```

For a local repository:

```bash
uv run demiurge package repo add ./local-packages --alias local --trust
```

Trust is explicit because repositories can install executable local code.

## Verify

```bash
uv run demiurge package list --core assistant
uv run demiurge init --check
uv run demiurge --provider fake
```

If the package installs a tool, inspect the visible tool registry:

```text
/tools
```

## Boundary

Package management is a user-controlled CLI workflow. It is not an agent-callable
model tool. Package recipes do not install Python dependencies or edit the host
`uv.lock`.
