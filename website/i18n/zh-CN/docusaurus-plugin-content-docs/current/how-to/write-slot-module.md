---
title: 编写 Agent Slot
description: 向 Agent Core 添加 bootstrap、input 或 output 行为。
---

# 编写 Agent Slot

当 core 需要在 agent loop 中某个受治理的位置加入 authored behavior 时，使用
Agent Slot：

- `bootstrap` 在每个 session 中添加一次 session-stable context。
- `input` 在 provider call 之前塑造当前 turn。
- `output` 在 provider call 之后处理最终 model output。

本指南编辑一个具体 runtime core。默认 runtime layout 中，core 位于：

```text
~/.demiurge/agents/<core_id>/
```

向某个 core 添加 slot 时，不要编辑 `~/.demiurge/agents/agent.yaml`。这个文件是
global fallback config。具体 core 有自己的 `agent.yaml` 和 authored surface，例如
`agent/`。

## 开始前

检查 runtime cores 是否能加载：

```bash
uv run demiurge init --check
```

打开目标 core，确认它有：

```text
agent.yaml
agent/pipelines.yaml
```

Core 的 `runtime.surface_root` 通常是 `agent`。Bootstrap、input 和 output slot
roots 会从这个 surface root 解析：

| Phase | Root |
| --- | --- |
| `bootstrap` | `agent/bootstrap/<slot_id>/` |
| `input` | `agent/input/<slot_id>/` |
| `output` | `agent/output/<slot_id>/` |

修改 `agent.yaml` 中的 `slots.input` 或 `slots.output` 不会移动这些 phase roots。

## 选择 Slot Phase

按你需要的行为选择 phase：

| 需求 | 使用 |
| --- | --- |
| 在 turns 之前加入 memory、identity 或 session-stable context | `bootstrap` |
| 在 model call 前添加 instructions、规范化 raw input、检查 attachments 或激活 skills | `input` |
| 在 model call 后发送 response、转换 output、发送 artifacts、写 result data 或更新 state | `output` |
| 暴露一个可被 model 显式调用的动作 | Authored tool，不是 slot |
| 在 slots/tools 之间共享 helper code | `agent/lib/`，不是 slot |

优先添加一个命名 slot 并更新 `agent/pipelines.yaml`。不要重写 `base_input` 或
`base_output`，除非这次更改就是要替换 seed input 或 output 行为。

## 创建 Slot 目录

对于名为 `style_hint` 的 input slot，创建：

```text
agent/input/style_hint/
  module.py
  slot.yaml
```

目录名就是 `agent/pipelines.yaml` 中使用的 slot id。

## 编写 `module.py`

默认 entrypoint 是相对于 slot 目录的 `module:process`。Callable 可以是同步函数，
也可以是异步函数。

### Bootstrap 示例

```python
def process(ctx):
    ctx.bootstrap.add("Session note: prefer concise, concrete answers.")
```

Bootstrap slots 在每个 session 的 turns 开始前运行一次。Bootstrap return values 会被
忽略；请通过 `ctx.bootstrap.add(...)` 写入 session-stable context。

### Input 示例

```python
def process(ctx):
    ctx.input.add_context(
        "For this turn, prefer short answers with concrete next steps.",
        role="system",
    )
```

Input slots 在 provider call 之前运行。Seed `base_input` slot 会追加原始用户文本：

```python
def process(ctx):
    ctx.input.add_context(ctx.input.raw_text, role="user")
```

如果没有 input slot 生成 user text，turn 会失败。

### Output 示例

```python
def process(ctx):
    ctx.output.send_text(ctx.output.response_text)
```

Output slots 在 provider response 之后运行。Seed `base_output` slot 会发送 model
response。如果你移除或跳过 `base_output`，其他 output slot 必须负责发送或记录
response。

## 使用常见 `ctx` APIs

完整参数参考见 [Slot Context SDK](../reference/slot-context-sdk.md)。

### 读取输入文本和 Attachments

```python
def process(ctx):
    if ctx.input.attachments:
        ctx.input.add_context(
            f"The user attached {len(ctx.input.attachments)} item(s).",
            role="system",
        )
```

### 发送文本或 Artifacts

```python
def process(ctx):
    ctx.output.send_text(
        "Archive complete.",
        history_policy="model_hidden",
        history_text="The archive step completed.",
    )
    ctx.output.send_file(
        "reports/summary.pdf",
        caption="Summary report",
        media_type="application/pdf",
        history_text="Sent the summary report PDF.",
    )
```

Artifact paths 必须位于 workspace 或 session artifact root 内。会写入 history 的
non-text deliveries 应该提供 `history_text`。

### 发送状态

```python
def process(ctx):
    ctx.output.progress("Preparing audio...")
    ctx.output.notice("Audio generation skipped because no voice is configured.")
```

`progress(...)` 和 `notice(...)` 是 transient status deliveries。它们不会写入
assistant history。

### 读取 Session History

```python
def process(ctx):
    recent = ctx.history.recent_messages(4, roles={"user", "assistant"})
    summary = "\n".join(f"{item.role}: {item.content}" for item in recent)
    ctx.input.add_context(f"Recent conversation:\n{summary}", role="system")
```

`ctx.history` 存在于 input 和 output slots。Bootstrap slots 和 authored tools 不会收到它。

### 写 Core 或 Session State

```python
def process(ctx):
    count = ctx.state.session.get("summary_count", 0)
    ctx.state.session.set("summary_count", count + 1)
    ctx.state.core.merge("preferences", {"summary_style": "short"})
```

State reads 和 writes 需要 `state.session.read`、`state.session.write`、
`state.core.read` 或 `state.core.write` 等 capabilities。

### 调用 Tool

```python
async def process(ctx):
    result = await ctx.tools.call("tools_list")
    ctx.output.notice(result.content[:200])
```

Slot 需要 `tool.call:<tool_name>` capability，且该 tool 必须对当前 core 可见。

### 运行 Child Agent

```python
async def process(ctx):
    result = await ctx.agents.run(
        "assistant",
        "Summarize this response for the parent output slot.",
        input_slots=["base_input"],
        output_slots=["base_output"],
        tools="none",
    )
    ctx.result.set({"child_summary": result.content})
```

当 child 应该作为后台 `agent.spawn` task 继续运行时，使用 `ctx.agents.spawn(...)`
而不是 `run(...)`。

### 激活 Skill

```python
def process(ctx):
    ctx.skills.activate("release-checklist")
```

只有 input slots 会收到 `ctx.skills`。Slot 需要 `skill.activate` 或
`skill.activate:<skill>` capability。

## 声明 `slot.yaml`

在 `module.py` 旁边创建 `slot.yaml`：

```yaml
entrypoint: module:process
description: "Adds a current-turn style hint."
input_schema: {}
capabilities: []
timeout_seconds: null
failure_policy: soft
default_placement: pre_current_user
history_policy: persist
```

可接受字段严格如下：

| Field | Default | Notes |
| --- | --- | --- |
| `entrypoint` | `module:process` | `module:function`，或 core-root-relative Python file path 加 function。 |
| `description` | `""` | 用于检查的人类可读描述。 |
| `input_schema` | `{}` | Author metadata；slot loader 会接受它。 |
| `capabilities` | `[]` | 这个 slot 可能通过 `ctx.capability.require(...)` 或 SDK clients 需要的 capabilities。 |
| `timeout_seconds` | `null` | 作为 metadata 加载；当前 slot invoker 不强制 timeout。 |
| `failure_policy` | `soft` | `soft` 会记录日志并继续；`hard` 会让 turn 或 bootstrap 失败。 |
| `default_placement` | `pre_current_user` | 面向 legacy context contribution shapes 的默认 placement metadata。 |
| `history_policy` | `persist` | output/tool-style sends 的默认 delivery history policy。 |

未知字段会被拒绝。

使用 guarded APIs 前，先声明 capabilities：

```yaml
capabilities:
  - state.session.read
  - state.session.write
  - tool.call:tools_list
  - agents.run:assistant
```

Capability grants 不会绕过 host approval、workspace scope、command guards、
channel policy 或 tool runtime rules。

## 将 Slot 添加到现有 Pipeline

打开现有的 `agent/pipelines.yaml`。把新的 slot id 插入对应的现有列表。

对于应该在追加原始用户文本之前运行的 input slot：

```yaml
input:
  serial:
    - style_hint
    - base_input
```

对于应该在 seed output delivery 之后运行的 output slot：

```yaml
output:
  serial:
    - base_output
    - archive_summary
```

对于 bootstrap slot：

```yaml
bootstrap:
  serial:
    - session_context
```

不要替换整个文件。除非这次更改有意修改，否则保留当前的 `schema_version`、
`bootstrap`、其他 phase entries 和任何现有 `parallel` 列表。

## 选择 Serial 或 Parallel

当 slot 必须影响主流程时，使用 `serial`。Serial input slots 可以修改 prompt。
Serial output slots 可以写入 history 并设置 `ctx.result`。

只把 `parallel` 用于后台副作用：

- Parallel input slots 不能修改当前 prompt。
- Parallel output slots 不能写入 session history。
- Parallel output slots 不能修改 `ctx.result`。

Bootstrap 只支持 `serial`。

## 验证

运行：

```bash
uv run demiurge init --check
uv run demiurge --provider fake
```

如果 core 加载失败，请对照 [Agent Slot contract](../reference/contracts/slot-modules.md)
检查 slot 目录。对于 evolution worktrees，请把编辑限制在 authored surface 内，并遵循
[evolver-safe edit contract](../reference/contracts/evolver-safe-edits.md)。
