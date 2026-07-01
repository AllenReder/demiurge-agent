---
title: Authored Surface 契约
description: Agent Core 拥有的文件的稳定规则。
---

# Authored Surface 契约

本页定义 Demiurge Agent Core 的 authored surface。它面向人类作者，也面向在项目文档
作为只读 reference context 提供时的 `evolver` core。

## Core Root

一个具体 runtime core 的形状如下：

```text
<core>/
  agent.yaml
  packages.yaml
  agent/
    SOUL.md
    bootstrap/
    input/
    output/
    tools/
    skills/
    schedules/
    mcp/
    lib/
    tests/
```

`packages.yaml` 是 package install state。除非用户明确要求你修复 package state，
否则不要手动编辑它。

## Agent Core 拥有

Agent Core 作者可以编辑：

- `agent.yaml`
- `agent/SOUL.md`
- `agent/bootstrap/`
- `agent/input/`
- `agent/output/`
- `agent/tools/`
- `agent/skills/`
- `agent/schedules/`
- `agent/mcp/`
- `agent/lib/`
- `agent/tests/`

## Host 拥有

Agent Core 作者不能接管：

- provider request construction
- provider calls
- session、turn 和 step storage
- tool registry and dispatch
- approval decisions
- workspace enforcement
- production state mutation
- package repository trust
- dependency installation
- promotion or rollback

## Dependency Rule

当前 runtime mode 是 `host_shared`。Agent Slot code 运行在 host Python
environment 中。Candidate cores 不能自动添加 Python dependencies。如果某次修改需要
dependency，把它记录为 manual dependency review item。

## 验证

Authored-surface edits 之后运行：

```bash
uv run demiurge init --check
uv run demiurge --provider fake
```

编辑 packages、schedules、MCP servers 或 tools 时，使用对应页面里的更窄检查。
