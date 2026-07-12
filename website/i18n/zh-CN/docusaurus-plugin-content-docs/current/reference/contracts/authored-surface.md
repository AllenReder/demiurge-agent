---
title: Authored Surface 合约
description: Agent Core 拥有的文件的稳定规则。
---

# Authored Surface 合约

这个 contract 定义 Demiurge Agent Core 的 authored surface。它面向人类 authors，也面向 `evolver` core（当 docs 作为 read-only reference context 提供时）。

## Core Identity

全局 fallback config 不是 Agent Core：

```text
agents/agent.yaml
```

具体 Agent Core 有自己的 manifest 和 authored surface：

```text
agents/<core>/
  agent.yaml
  agent/
    SOUL.md
    pipelines.yaml
    bootstrap/
    input/
    output/
    tools/
    skills/
    schedules/
    mcp/
    lib/
```

同样的形状存在于 runtime home 下的 `~/.demiurge/agents/<core>/`。

## Loader Contract

对于具体 core，loader 要求：

- `<core>/agent.yaml`
- `runtime.surface_root` 命名的目录
- `<surface_root>/pipelines.yaml`

使用默认 `runtime.surface_root: agent` 时，bootstrap、input 和 output slot roots 是：

```text
agent/bootstrap/
agent/input/
agent/output/
```

这些 phase roots 不会被 `slots.input` 或 `slots.output` 移动。

Skills、schedules 和 MCP roots 会推断为 `agent/skills`、`agent/schedules` 和 `agent/mcp`，除非配置了 `slots.skills`、`slots.schedules` 或 `slots.mcp`。Authored tools 会从配置好的 `slots.tools` root 发现。

Authored tool id 不得与已选择的 builtin tool name 冲突。Loader 会报告两侧 provenance
并失败，而不是应用 source 优先级。MCP name collision 会在构建最终 per-turn catalog 时被拒绝。

## Core-Owned Files

Agent Core authors 可以编辑：

- `agent.yaml`
- `agent/SOUL.md`
- `agent/pipelines.yaml`
- `agent/bootstrap/`
- `agent/input/`
- `agent/output/`
- `agent/tools/`
- `agent/skills/`
- `agent/schedules/`
- `agent/mcp/`
- `agent/lib/`

`packages.yaml` 是 package provenance state。它记录 installed package targets 和 hashes，但不是 runtime truth。只有在明确修复 package state 时才编辑它。

## Host-Owned Systems

Agent Core authors 不得接管：

- provider request construction
- provider profile resolution、provider-native request construction 和
  provider wire protocol conversion
- provider calls
- session、turn、step、message、artifact 和 runtime event storage
- tool registry and dispatch
- approval decisions
- workspace enforcement
- production state mutation
- package repository trust
- dependency installation
- Git revision promotion or rollback
- gateway channel transport
- scheduler claims and run logs

## Slot Contract

Bootstrap、input 和 output slots 会把 code 与 metadata 放在一起：

```text
agent/input/<slot_id>/
  module.py
  slot.yaml
```

Phase order 只存在于 `agent/pipelines.yaml`。

`base_input`、`base_output` 和 `session_context` 是默认 core 中可编辑的 seed slots。它们不是隐藏的 host built-ins。

## Tool Contract

Authored tools 是公开的 Agent Core 文件：

```text
agent/tools/<tool_id>/
  tool.yaml
  module.py
```

它们是 model-callable actions，不是 pipeline slots。Tool 的单数 `capability` 是 registry 和 approval metadata。它的 `capabilities` 列表让 implementation 可以满足 `ctx.capability.require(...)`。

## Dependency Rule

当前 runtime mode 是 `host_shared`。Authored Python code 运行在 host 的 uv-managed environment 中。

Candidate cores 不得自动添加 Python dependencies。如果某个 change 需要 dependency，请把它记录为 manual dependency review item。

## Verification

Authored-surface edits 之后，运行：

```bash
uv run demiurge init --check
uv run demiurge --provider fake
```

编辑 tools、schedules、MCP servers 或 channels 时，使用相关 how-to 或 reference page 中更窄的 checks。
