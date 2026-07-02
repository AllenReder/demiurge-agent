---
sidebar_position: 2
title: memory_honcho
description: 安装并配置内置 Honcho-backed memory package。
---

# memory_honcho

`memory_honcho` 为 Agent Core 添加 Honcho-backed persistent memory。它会在
model calls 前注入相关 memory context，在 assistant response 完成后把 turn 镜像到
Honcho，并可选暴露 model-callable `honcho_*` tools。

当你需要由 Honcho 支撑的 cross-session user 或 project recall，而不是只使用
file-backed [`memory_basic`](memory_basic.md) package 时，使用它。

## 安装内容

Package 会把 package-owned authored components 安装进选中的 runtime core：

```text
agent/lib/memory_honcho/
agent/bootstrap/memory_honcho/
agent/input/memory_honcho_recall/
agent/output/memory_honcho_sync/
agent/skills/memory_honcho/
```

`enable_tools=true` 时，也就是默认情况，它还会安装：

```text
agent/tools/honcho_profile/
agent/tools/honcho_search/
agent/tools/honcho_context/
agent/tools/honcho_reasoning/
agent/tools/honcho_conclude/
```

它还会更新 `agent/pipelines.yaml`：

```yaml
bootstrap:
  serial:
    - session_context
    - memory_honcho
input:
  serial:
    - base_input
    - memory_honcho_recall
  parallel: []
output:
  serial:
    - base_output
  parallel:
    - memory_honcho_sync
```

Runtime memory data 不属于 package ownership，存储在：

```text
memory/honcho/
  cache.json
  outbox.jsonl
  synced_turns.json
```

Uninstall 会移除已安装 slots、tools、skill 和 lib files，但保留
`memory/honcho/`。

## Requirements

`memory_honcho` 声明一个 manual dependency：

```text
honcho-ai
```

Demiurge packages 不安装 Python dependencies，也不修改 `uv.lock`。使用该 package
前，按 host environment policy 安装 `honcho-ai`。

Package 可以连接 Honcho Cloud 或 self-hosted Honcho endpoint：

| Setup | 必需配置 |
| --- | --- |
| Honcho Cloud | `HONCHO_API_KEY` 或 `api_key` package option |
| Self-hosted 或 local Honcho | `HONCHO_BASE_URL` 或 `base_url` package option |
| Self-hosted with auth | `base_url` 加 `api_key` |

当设置了 `base_url` 但没有设置 `api_key` 时，package 会向 Honcho SDK 传入
`api_key="local"`。

## 安装

先 preview：

```bash
uv run demiurge package install memory_honcho --core assistant --preview
```

使用环境变量安装：

```bash
export HONCHO_API_KEY=...
uv run demiurge package install memory_honcho --core assistant
```

Local 或 self-hosted Honcho service：

```bash
export HONCHO_BASE_URL=http://localhost:8000
uv run demiurge package install memory_honcho --core assistant
```

也可以在安装时传入 options：

```bash
uv run demiurge package install memory_honcho \
  --core assistant \
  --option api_key=... \
  --option workspace=demiurge \
  --option peer_name=allen \
  --option session_strategy=per-directory
```

Secret options 会在 `packages.yaml` 中 redacted。

## Recall Modes

`recall_mode` 控制 memory 是自动注入、通过 tools 暴露，还是两者都启用。

| Mode | 行为 |
| --- | --- |
| `hybrid` | 默认。自动注入 Honcho context，并在 `enable_tools=true` 时安装 `honcho_*` tools。 |
| `context` | 自动注入 Honcho context。除非设置 `enable_tools=false`，tools 仍会安装；如果你想要 context-only 行为，请使用该 option。 |
| `tools` | 不自动注入 Honcho context。`enable_tools=true` 时安装 tools。 |

安装 tools-only mode：

```bash
uv run demiurge package install memory_honcho \
  --core assistant \
  --option recall_mode=tools
```

禁用 tool 安装：

```bash
uv run demiurge package install memory_honcho \
  --core assistant \
  --option enable_tools=false
```

## Options

| Option | Default | 说明 |
| --- | --- | --- |
| `recall_mode` | `hybrid` | `hybrid`、`context` 或 `tools`。 |
| `enable_tools` | `true` | 为显式调用安装 `honcho_*` authored tools。 |
| `api_key` | unset | 直接传入 Honcho API key。Fallback 到 `HONCHO_API_KEY`。 |
| `base_url` | unset | Honcho API base URL。Fallback 到 `HONCHO_BASE_URL`。用于 self-hosted 或 local Honcho。 |
| `workspace` | `demiurge` | Honcho workspace id。 |
| `peer_name` | unset | 稳定的 user peer id。未设置时，Demiurge 会从 turn metadata 或 session id 派生。 |
| `ai_peer` | `demiurge-assistant` | assistant 的 Honcho peer id。 |
| `session_strategy` | `per-directory` | Demiurge turns 到 Honcho sessions 的映射方式。可选 `per-directory`、`per-repo`、`per-session`、`global`。 |
| `context_tokens` | `1200` | 解析为目标 context budget。当前格式化仍按 section 组织，尚未执行硬 token truncation。 |
| `timeout_seconds` | `3` | 在 SDK 支持时传给 Honcho SDK。 |
| `context_cadence` | `1` | 接受为 package configuration。当前实现尚未执行 turn-gap throttle。 |

## Session Mapping

Package 使用 `session_strategy` 把每个 turn 映射到 Honcho session：

| Strategy | Honcho session id |
| --- | --- |
| `per-directory` | 当前 workspace directory basename。 |
| `per-repo` | 当前 workspace directory basename。目前行为等同于 `per-directory`。 |
| `per-session` | Demiurge session id。 |
| `global` | 已配置的 `workspace` value。 |

当同一个 human 需要跨 sessions 和 workspaces 保持同一个 Honcho peer 时，设置
`peer_name`。当多个 Agent Cores 在同一个 Honcho workspace 中需要不同 assistant
identities 时，设置 `ai_peer`。

## Runtime 行为

`memory_honcho` 在 model call 前后使用三个 slots：

| Slot | Timing | 行为 |
| --- | --- | --- |
| `bootstrap/memory_honcho` | Session bootstrap | 添加静态 `# Honcho Memory` guidance 和未过期 cached Honcho context。不会 fetch remote context。 |
| `input/memory_honcho_recall` | `base_input` 前 | 在 `hybrid` 和 `context` modes 下 fetch 当前 Honcho context，并把它作为 transient system input 注入。 |
| `output/memory_honcho_sync` | Parallel output | 把 completed turn 追加到 `outbox.jsonl`，drain pending outbox records 到 Honcho，并刷新 `cache.json` 给下一 turn 使用。 |

注入的 memory 会包在 `<memory-context>` 中，并标记为 background data，而不是新的
user input。Completed turns 会在 sync 前 sanitize，避免泄露的 `<memory-context>`
blocks 写回 Honcho。

所有 slots 使用 `failure_policy: soft`。如果 Honcho 缺失、配置错误、很慢或不可用，
Demiurge 会继续主 turn。Output slot 会把 pending records 保留在 `outbox.jsonl`，让后续成功运行可以 drain。

## Tools

当 `enable_tools=true` 时，package 会安装五个 authored tools：

| Tool | Approval | 用途 |
| --- | --- | --- |
| `honcho_profile` | `auto` | 读取或替换 peer card。省略 `card` 表示读取；传入 string list `card` 表示替换。 |
| `honcho_search` | `auto` | 搜索某个 peer 的 raw Honcho memory context。需要 `query`。 |
| `honcho_context` | `auto` | 获取当前 session summary、representation 和 peer card。 |
| `honcho_reasoning` | `auto` | 让 Honcho 合成回答。需要 `query`；接受可选 `reasoning_level`。 |
| `honcho_conclude` | `prompt` | 写入或删除 persistent conclusion。必须只传 `conclusion` 或 `delete_id` 之一。 |

所有 tools 都需要 `network.fetch`，`risk: medium`，并返回 model-visible JSON
content。每个 tool 接受可选 `peer`，其中 `user` 映射到 user peer，`ai` 或
`assistant` 映射到 assistant peer。

## 验证

列出已安装 packages：

```bash
uv run demiurge package list --core assistant
```

运行 fake-provider turn，确认 core 仍可加载：

```bash
uv run demiurge --provider fake
```

在 TUI 中检查 tools：

```text
/tools
```

配置 Honcho 并完成真实 turn 后，检查：

```text
~/.demiurge/agents/assistant/memory/honcho/cache.json
~/.demiurge/agents/assistant/memory/honcho/synced_turns.json
```

如果 Honcho 不可用，`outbox.jsonl` 可能会一直存在，直到后续运行成功 drain。

## 卸载

Preview removal：

```bash
uv run demiurge package uninstall memory_honcho --core assistant --preview
```

Uninstall：

```bash
uv run demiurge package uninstall memory_honcho --core assistant
```

Uninstall 会恢复 bootstrap、input 和 output pipelines，并移除 package-owned
component directories。它不会移除 `memory/honcho/`。

## 与 Hermes Honcho 的区别

这个 package 遵循与 Hermes Honcho memory 类似的大体形态：static memory
guidance、automatic recall、completed-turn sync 和显式 Honcho tools。

实现边界不同：

- Demiurge 使用 package-owned slots 和 lib code。它不会新增 host harness
  lifecycle extension points。
- 没有 `hermes memory setup` 等价命令。通过 package options、环境变量和已安装的
  `config.yaml` 配置该 package。
- Package 不会安装 `honcho-ai`。
- Bootstrap 只使用 cached context。Remote recall 发生在 input slot。
- Turn sync 使用本地 durable outbox，因为 package slots 不拥有 long-lived provider thread。
