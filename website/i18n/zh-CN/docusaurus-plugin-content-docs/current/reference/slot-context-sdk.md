---
title: Slot Context SDK
description: Bootstrap、input 和 output slots 中 ctx 对象的参考。
---

# Slot Context SDK

Agent Slots 会从 host 收到一个 `ctx` 对象。这个对象取决于 slot phase。
Bootstrap slots 在 turn 存在之前运行，所以只收到 session 级上下文。Input 和
output slots 在 turn 内运行，所以会收到 turn、history、state、tools 和 child-agent
clients。

## Context 可用性

| Attribute | Bootstrap | Input | Output | 含义 |
| --- | --- | --- | --- | --- |
| `ctx.session_id` | 有 | 通过 `ctx.turn` | 通过 `ctx.turn` | 当前 session id。 |
| `ctx.core_id` | 有 | 通过 `ctx.turn` | 通过 `ctx.turn` | 当前 Agent Core id。 |
| `ctx.core_revision` | 有 | 通过 `ctx.turn` | 通过 `ctx.turn` | 当前 Agent Core Git revision。 |
| `ctx.workspace` | 有 | 通过 `ctx.input.workspace` | 通过 `ctx.output.workspace` | 解析后的 workspace root。 |
| `ctx.turn` | 无 | 有 | 有 | Turn metadata。 |
| `ctx.slot_id` | 有 | 有 | 有 | 当前 slot 的目录名。 |
| `ctx.slot_path` | 有 | 有 | 有 | Core-relative slot path，例如 `agent/input/style_hint`。 |
| `ctx.capability` | 有 | 有 | 有 | 提供 `can(...)` 和 `require(...)` 的 capability facade。 |
| `ctx.bootstrap` | 有 | 无 | 无 | Bootstrap context writer。 |
| `ctx.input` | 无 | 有 | 无 | 当前 turn 的 input builder 和 input delivery client。 |
| `ctx.output` | 无 | 无 | 有 | Provider response delivery client。 |
| `ctx.history` | 无 | 有 | 有 | 当前 session history reader。 |
| `ctx.state` | 无 | 有 | 有 | Host-managed core/session state client。 |
| `ctx.tools` | 无 | 有 | 有 | Host tool-call client。 |
| `ctx.agents` | 无 | 有 | 有 | Child-agent run/spawn client。 |
| `ctx.skills` | 无 | 有 | 无 | Skill activation client。 |
| `ctx.result` | 无 | 无 | 有 | 当前 turn 的 structured result writer。 |

Bootstrap slots 不会收到 `ctx.history`、`ctx.state`、`ctx.tools`、
`ctx.agents`、`ctx.skills` 或 `ctx.result`。

## Turn Metadata

Input 和 output slots 可以读取 `ctx.turn`：

| Field | Type | 含义 |
| --- | --- | --- |
| `session_id` | `str` | 当前 session id。 |
| `turn_id` | `str` | 当前 turn id。 |
| `core_id` | `str` | 当前 Agent Core id。 |
| `core_revision` | `str` | 当前 live Agent Core revision。 |
| `user_input.content` | `str` | 当前 turn 的原始 inbound text。 |
| `user_input.metadata` | `dict` | 附着在 inbound 上的 host/channel metadata。 |
| `metadata` | `dict` | Runtime 提供的 turn metadata。 |

## Bootstrap Client

Bootstrap slots 添加 session-stable context：

```python
def process(ctx):
    ctx.bootstrap.add("Remember that this session is about release prep.")
```

| Method | 含义 |
| --- | --- |
| `ctx.bootstrap.add(text)` | 把非空文本加入 bootstrap snapshot。 |

Bootstrap return values 会被忽略。所有 bootstrap 文本都要通过
`ctx.bootstrap.add(...)` 写入。

## Input Client

Input slots 在 provider call 之前塑造当前 turn。

| Attribute or method | 含义 |
| --- | --- |
| `ctx.input.raw_text` | 原始 inbound text。 |
| `ctx.input.attachments` | Inbound attachment metadata tuple。 |
| `ctx.input.workspace` | 解析后的 workspace root，类型为 `Path`。 |
| `ctx.input.session_root` | Session artifact root，类型为 `Path`。 |
| `ctx.input.add_context(content, role="system", write_history=None)` | 向当前 prompt 添加 `system` 或 `user` 文本。 |
| `ctx.input.add(section, content, history_policy=None)` | 更底层的 prompt add；`section` 必须是 `system` 或 `user`。 |

`add_context(..., role="system")` 默认是 transient input context。
`add_context(..., role="user")` 默认写入 persisted user history。

Seed `base_input` slot 会把 `ctx.input.raw_text` 追加为 user message。添加
hint 的自定义 input slots 通常放在 `base_input` 之前。

Parallel input slots 不能修改当前 prompt。在 parallel input slot 中调用
`ctx.input.add_context(...)` 或 `ctx.input.add(...)` 会抛出 `RuntimeError`。

## Output Client

Output slots 在 model/tool loop 之后处理 provider response。

| Attribute or method | 含义 |
| --- | --- |
| `ctx.output.response_text` | 最终 provider response text。 |
| `ctx.output.content` | 与 `response_text` 相同的文本。 |
| `ctx.output.metadata` | Turn interaction metadata。 |
| `ctx.output.workspace` | 解析后的 workspace root，类型为 `Path`。 |
| `ctx.output.session_root` | Session artifact root，类型为 `Path`。 |

Seed `base_output` slot 会发送 `ctx.output.response_text`。如果 pipeline 省略
`base_output`，其他 output slot 必须负责发送或记录 response。

## Delivery Methods

Input 和 output clients 暴露同一组 delivery methods。Input deliveries 默认使用
transient history，因为 input slots 在 assistant response 之前运行。Output
deliveries 默认使用 slot 的 `history_policy`。

### `send_text`

```python
ctx.output.send_text(
    text,
    write_history=None,
    history_policy=None,
    visible=True,
    history_text=None,
    failure_history_text=None,
    delivery_metadata=None,
)
```

| Parameter | 含义 |
| --- | --- |
| `text` | 要发送的 text block。 |
| `write_history` | `True` 映射到 `persist`；`False` 映射到 `transient`，除非设置了 `history_policy`。 |
| `history_policy` | `persist`、`model_hidden` 或 `transient`。 |
| `visible` | Delivery 是否对用户可见。 |
| `history_text` | 写入 history 的文本；默认是 `text`。 |
| `failure_history_text` | 可供失败历史处理使用的文本。 |
| `delivery_metadata` | 附加到 delivery request 的 metadata。 |

### `send_image`、`send_audio`、`send_video`、`send_file`

```python
ctx.output.send_file(
    source,
    caption=None,
    media_type=None,
    summary=None,
    artifact_metadata=None,
    write_history=None,
    history_policy=None,
    visible=True,
    history_text=None,
    failure_history_text=None,
    delivery_metadata=None,
)
```

这些 artifact helpers 共享同一组参数：

| Parameter | 含义 |
| --- | --- |
| `source` | Workspace/session path、URL 或 `ArtifactRef`。 |
| `caption` | 随 artifact block 显示的文本。 |
| `media_type` | MIME type hint。 |
| `summary` | Host 存储的 artifact summary。 |
| `artifact_metadata` | 随 artifact 存储的 metadata。 |
| `write_history`, `history_policy`, `visible`, `history_text`, `failure_history_text`, `delivery_metadata` | 与 `send_text` 相同的 history 和 delivery controls。 |

本地 artifact paths 必须位于 `ctx.input.workspace`、`ctx.output.workspace` 或
对应的 `session_root` 内。会写入 history 的 artifact deliveries 应该提供
`history_text`；否则后续 model context 没有可用的文本表示。

### `progress` 和 `notice`

```python
ctx.output.progress("Still working...")
ctx.output.notice("Skipped optional indexing step.")
```

| Method | 含义 |
| --- | --- |
| `progress(text, visible=True, delivery_metadata=None)` | 发送 transient progress。 |
| `notice(text, visible=True, delivery_metadata=None)` | 发送 transient notice。 |

`progress` 和 `notice` 总是使用 transient history，不接受 `history_policy`。

## History Client

Input 和 output slots 可以读取当前 session history：

```python
messages = ctx.history.recent_messages(5, roles={"user", "assistant"})
for message in messages:
    ctx.output.notice(f"{message.role}: {message.content[:80]}")
```

| API | 含义 |
| --- | --- |
| `ctx.history.recent_messages(limit, roles=None)` | 从当前 session 返回最近的 `HistoryMessageSummary` items。 |

`roles` 默认是 `{"user", "assistant", "tool"}`。其他 roles 会被忽略。非正数
`limit` 返回空列表。

`HistoryMessageSummary` 字段：

| Field | 含义 |
| --- | --- |
| `message_id` | Session message id。 |
| `role` | `user`、`assistant` 或 `tool`。 |
| `content` | 存储的 message text。 |
| `turn_id` | 产生该 message 的 turn。 |
| `created_at` | 创建时间字符串。 |
| `step_id` | Model/tool-loop step id。 |
| `tool_call_id` | Tool result message 的 tool call id。 |
| `tool_calls` | Assistant message 上附带的 tool calls。 |
| `visible` | Message 是否对用户可见。 |
| `model_visible` | 后续 provider context 是否可以包含该 message。 |
| `tool_name` | Tool result message 的 tool name。 |
| `is_error` | Tool result message 的 tool error flag。 |

## State Client

Input 和 output slots 可以使用 host-managed state：

```python
count = ctx.state.session.get("draft_count", 0)
ctx.state.session.set("draft_count", count + 1)
ctx.state.core.merge("preferences", {"tone": "concise"})
```

`ctx.state.core` 以 Agent Core 为 scope。`ctx.state.session` 以当前 session 为
scope。

| Method | Required capability | 含义 |
| --- | --- | --- |
| `get(target, default=None)` | `state.core.read` 或 `state.session.read` | 读取一个 target。 |
| `set(target, value)` | `state.core.write` 或 `state.session.write` | 替换一个 target。 |
| `merge(target, value)` | Write capability | 把 object merge 到一个 target。 |
| `append(target, value)` | Write capability | 向一个 target append 一个值。 |
| `snapshot()` | Read capability | 返回该 scope 的完整 state snapshot。 |

如果配置了 `state.session.write:draft_count` 这样的 target-specific grant，它可以
满足对应 target 的操作。否则需要 generic scope capability。

## Tools Client

Input 和 output slots 可以调用 visible tools：

```python
result = await ctx.tools.call("project_note", {"topic": "release"})
ctx.output.notice(result.content)
```

| API | Required capability | 含义 |
| --- | --- | --- |
| `await ctx.tools.call(name, arguments=None)` | `tool.call:<name>` | 执行一个 visible host、authored 或 MCP tool。 |

Tool 必须对当前 core 可见，并符合正常 host capability 和 approval policy。

## Child Agent Client

Input 和 output slots 可以运行 child agents：

```python
result = await ctx.agents.run(
    "assistant",
    "Summarize this for the parent slot.",
    input_slots=["base_input"],
    output_slots=["base_output"],
    tools="none",
)

handle = ctx.agents.spawn(
    "assistant",
    "Review this later.",
    input_slots="all",
    output_slots="all",
    tools=["tools_list"],
    use_bootstrap=True,
)
```

| API | Required capability | 含义 |
| --- | --- | --- |
| `await ctx.agents.run(core_id, raw_input, ...)` | `agents.run:<core_id>` | 运行 child turn 并等待 `AgentRunResult`。 |
| `ctx.agents.spawn(core_id, raw_input, ...)` | `agents.spawn:<core_id>` | 启动一个 `agent.spawn` background task，并返回 `AgentSpawnHandle`。 |

两个调用都接受：

| Parameter | 含义 |
| --- | --- |
| `context` | 注入 child turn 的额外字符串或字符串列表。 |
| `input_slots` | `None`、`[]`、`"all"` 或非空 child input slot id 列表。 |
| `output_slots` | `None`、`[]`、`"all"` 或非空 child output slot id 列表。 |
| `use_bootstrap` | 默认 `False`；`True` 使用 child core bootstrap pipeline。 |
| `tools` | `"all"`、`"none"`、`[]` 或 child tool id 列表。 |

对 `input_slots` 和 `output_slots` 来说，省略、`None` 或 `[]` 只运行
`base_input` 或 `base_output`。`"all"` 运行 child core 的完整 configured
pipeline。非空列表会按 slot id 过滤 active child pipeline。

对 `tools` 来说，省略、`None` 或 `"all"` 使用 child core 的 configured tools。
`"none"` 或 `[]` 隐藏所有 child tools。非空列表会收窄 child core 的 configured
tools。

## Skills Client

Input slots 可以为当前 turn 激活 skills：

```python
def process(ctx):
    ctx.skills.activate("release-checklist")
```

Slot 需要 `skill.activate` 或 `skill.activate:<skill>` capability。未知 skill name
会在记录 activation request 后被忽略。

## Result Client

Output slots 可以为当前 turn 设置 structured result：

```python
def process(ctx):
    ctx.result.set({"summary": ctx.output.response_text[:200]})
```

| API | 含义 |
| --- | --- |
| `ctx.result.value` | 当前 result value；未设置时为 `None`。 |
| `ctx.result.set(value)` | 设置或 merge 一个 JSON-compatible result value。 |

Parallel output slots 不能修改 `ctx.result`。Result values 必须 JSON-compatible。
浮点数必须是 finite。

## Capability Checks

当 authored code 准备请求 host-mediated effect 时，使用 `ctx.capability`：

```python
def process(ctx):
    ctx.capability.require("fs.read", slot_path=ctx.slot_path)
```

| API | 含义 |
| --- | --- |
| `ctx.capability.can(capability, slot_path=ctx.slot_path)` | 返回 capability 是否已 grant。 |
| `ctx.capability.require(capability, slot_path=ctx.slot_path)` | 如果 capability 未 grant，则抛错。 |

在 `slot.yaml` 中声明 capability 会让该 grant 对 slot 可用。它不会绕过 host
approval、workspace scope、command guards、channel policy 或 tool runtime rules。
