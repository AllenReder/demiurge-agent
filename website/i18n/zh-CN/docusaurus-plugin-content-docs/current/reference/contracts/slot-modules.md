---
title: Agent Slot 合约
description: Bootstrap、input 和 output slots 的稳定规则。
---

# Agent Slot 合约

Agent Slots 是从 Agent Core 的 authored surface 加载的受治理 extension points。它们让 core-authored code 在 host-owned agent loop 的特定位置运行。

## Directory Contract

使用 `runtime.surface_root: agent` 时，slot directories 是：

```text
agent/bootstrap/<slot_id>/
  module.py
  slot.yaml
agent/input/<slot_id>/
  module.py
  slot.yaml
agent/output/<slot_id>/
  module.py
  slot.yaml
```

Loader 会从 `runtime.surface_root` 发现 bootstrap、input 和 output slots，而不是从 `slots.input` 或 `slots.output`。

## Manifest Contract

`slot.yaml` 只接受这些字段：

```yaml
entrypoint: module:process
description: "Short description."
input_schema: {}
capabilities: []
timeout_seconds: null
failure_policy: soft
default_placement: pre_current_user
history_policy: persist
```

未知字段会导致 core loading 失败。

## Entrypoint Contract

通常的 entrypoint 是：

```yaml
entrypoint: module:process
```

```python
def process(ctx):
    ...
```

Entrypoints 会从 slot 目录加载，除非 manifest 使用 core-root-relative Python file path。

Relative imports 按 slot path 隔离。共享 helper code 可以放在默认 authored surface 的 `agent/lib/` 下。

## Pipeline Contract

`agent/pipelines.yaml` 是必需的：

```yaml
schema_version: 1
bootstrap:
  serial: []
input:
  serial: []
  parallel: []
output:
  serial: []
  parallel: []
```

规则：

- `schema_version` 必须是 `1`。
- 每个 pipeline entry 都必须是该 phase 的已知 slot id。
- 同一个 pipeline 中，一个 slot id 只能出现一次。
- Bootstrap 只支持 `serial`。
- 未知 phases 和 pipeline keys 会导致 core loading 失败。

添加 slot 时，编辑现有列表，并保留无关 phases。

## Bootstrap Context

Bootstrap 在 turns 开始前，每个 session 运行一次：

```python
def process(ctx):
    ctx.bootstrap.add("Session-level context.")
```

Bootstrap return values 会被忽略。使用 `ctx.bootstrap.add(...)` 添加 session-stable context。

## Input Context

Input slots 在 provider call 之前运行：

```python
def process(ctx):
    ctx.input.add_context("Prefer concise answers.", role="system")
    ctx.input.add_context(ctx.input.raw_text, role="user")
```

Seed `base_input` slot 会追加原始用户文本。如果没有 input slot 生成 user text，turn 会失败。

Serial input slots 可以修改 prompt。Parallel input slots 不能修改当前 prompt。

## Output Context

Output slots 在 provider response 之后运行：

```python
def process(ctx):
    ctx.output.send_text(ctx.output.response_text)
```

Seed `base_output` slot 会发送 model response。如果没有 output slot 发送或记录 response，原始 provider response 只会保留在 runtime records 中。

Serial output slots 可以写入 history 和 result data。Parallel output slots 不能写入 session history，也不能修改当前 result。

## Capability Rule

当 slot code 需要 host-mediated effects 时，在 `slot.yaml` 中声明 capabilities：

```yaml
capabilities:
  - fs.read
  - tool.call:project_note
```

然后在代码中 require 它们：

```python
def process(ctx):
    ctx.capability.require("fs.read", slot_path=ctx.slot_path)
```

当某个 effect 已有 host capability 时，不要绕过 host tools、workspace scope、channel policy 或 state APIs。

## Failure Rule

可选行为使用 `failure_policy: soft`。只有当缺少该 slot 就无法继续 phase 时，才使用 `failure_policy: hard`。

## Verification

Slot edits 后运行：

```bash
uv run demiurge init --check
uv run demiurge --provider fake
```
