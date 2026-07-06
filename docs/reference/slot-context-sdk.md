---
title: Slot Context SDK
description: Reference for ctx objects passed to bootstrap, input, and output slots.
---

# Slot Context SDK

Agent Slots receive a `ctx` object from the host. The object depends on the
slot phase. Bootstrap slots run before a turn exists, so they receive only
session-level context. Input and output slots run inside a turn, so they receive
the turn, history, state, tools, and child-agent clients.

## Context Availability

| Attribute | Bootstrap | Input | Output | Meaning |
| --- | --- | --- | --- | --- |
| `ctx.session_id` | Yes | Via `ctx.turn` | Via `ctx.turn` | Current session id. |
| `ctx.core_id` | Yes | Via `ctx.turn` | Via `ctx.turn` | Active Agent Core id. |
| `ctx.core_revision` | Yes | Via `ctx.turn` | Via `ctx.turn` | Active Agent Core Git revision. |
| `ctx.workspace` | Yes | Via `ctx.input.workspace` | Via `ctx.output.workspace` | Resolved workspace root. |
| `ctx.turn` | No | Yes | Yes | Turn metadata. |
| `ctx.slot_id` | Yes | Yes | Yes | Directory name for the current slot. |
| `ctx.slot_path` | Yes | Yes | Yes | Core-relative slot path, such as `agent/input/style_hint`. |
| `ctx.capability` | Yes | Yes | Yes | Capability facade with `can(...)` and `require(...)`. |
| `ctx.bootstrap` | Yes | No | No | Bootstrap context writer. |
| `ctx.input` | No | Yes | No | Current-turn input builder and input delivery client. |
| `ctx.output` | No | No | Yes | Provider response delivery client. |
| `ctx.history` | No | Yes | Yes | Current-session history reader. |
| `ctx.state` | No | Yes | Yes | Host-managed core/session state client. |
| `ctx.tools` | No | Yes | Yes | Host tool-call client. |
| `ctx.agents` | No | Yes | Yes | Child-agent run/spawn client. |
| `ctx.skills` | No | Yes | No | Skill activation client. |
| `ctx.result` | No | No | Yes | Structured result writer for the current turn. |

Bootstrap slots do not receive `ctx.history`, `ctx.state`, `ctx.tools`,
`ctx.agents`, `ctx.skills`, or `ctx.result`.

## Turn Metadata

Input and output slots can read `ctx.turn`:

| Field | Type | Meaning |
| --- | --- | --- |
| `session_id` | `str` | Current session id. |
| `turn_id` | `str` | Current turn id. |
| `core_id` | `str` | Active Agent Core id. |
| `core_revision` | `str` | Current live Agent Core revision. |
| `user_input.content` | `str` | Raw inbound text for the turn. |
| `user_input.metadata` | `dict` | Host/channel metadata attached to the inbound. |
| `metadata` | `dict` | Turn metadata from the runtime. |

## Bootstrap Client

Bootstrap slots add session-stable context:

```python
def process(ctx):
    ctx.bootstrap.add("Remember that this session is about release prep.")
```

| Method | Meaning |
| --- | --- |
| `ctx.bootstrap.add(text)` | Adds non-empty text to the bootstrap snapshot. |

Bootstrap return values are ignored. Store all bootstrap text through
`ctx.bootstrap.add(...)`. Slot return values are not interpreted as host effect
requests; use the documented `ctx.input`, `ctx.output`, `ctx.state`,
`ctx.tools`, `ctx.agents`, and `ctx.result` clients instead.

## Input Client

Input slots shape the current turn before the provider call.

| Attribute or method | Meaning |
| --- | --- |
| `ctx.input.raw_text` | Raw inbound text. |
| `ctx.input.attachments` | Tuple of inbound attachment metadata. |
| `ctx.input.workspace` | Resolved workspace root as a `Path`. |
| `ctx.input.session_root` | Session artifact root as a `Path`. |
| `ctx.input.add_context(content, role="system", write_history=None)` | Add `system` or `user` text to the current prompt. |
| `ctx.input.add(section, content, history_policy=None)` | Lower-level prompt add; `section` must be `system` or `user`. |

`add_context(..., role="system")` defaults to transient input context.
`add_context(..., role="user")` defaults to persisted user history.

The seed `base_input` slot appends `ctx.input.raw_text` as the user message.
Custom input slots that add hints normally run before `base_input`.

Parallel input slots cannot modify the current prompt. Calling
`ctx.input.add_context(...)` or `ctx.input.add(...)` from a parallel input slot
raises `RuntimeError`.

## Output Client

Output slots handle the provider response after the model/tool loop.

| Attribute or method | Meaning |
| --- | --- |
| `ctx.output.response_text` | Final provider response text. |
| `ctx.output.content` | Same text as `response_text`. |
| `ctx.output.metadata` | Turn interaction metadata. |
| `ctx.output.workspace` | Resolved workspace root as a `Path`. |
| `ctx.output.session_root` | Session artifact root as a `Path`. |

The seed `base_output` slot delivers `ctx.output.response_text`. If a pipeline
omits `base_output`, another output slot must deliver or record the response.

## Delivery Methods

Input and output clients expose the same delivery methods. Input deliveries
default to transient history because input slots run before the assistant
response. Output deliveries default from the slot's `history_policy`.

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

| Parameter | Meaning |
| --- | --- |
| `text` | Text block to deliver. |
| `write_history` | `True` maps to `persist`; `False` maps to `transient` unless `history_policy` is set. |
| `history_policy` | One of `persist`, `model_hidden`, or `transient`. |
| `visible` | Whether the delivery is user-visible. |
| `history_text` | Text written to history; defaults to `text`. |
| `failure_history_text` | Text available for failure history handling. |
| `delivery_metadata` | Extra metadata attached to the delivery request. |

### `send_image`, `send_audio`, `send_video`, `send_file`

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

The artifact helpers share the same parameters:

| Parameter | Meaning |
| --- | --- |
| `source` | Workspace/session path, URL, or `ArtifactRef`. |
| `caption` | Text shown with the artifact block. |
| `media_type` | MIME type hint. |
| `summary` | Artifact summary stored by the host. |
| `artifact_metadata` | Metadata stored with the artifact. |
| `write_history`, `history_policy`, `visible`, `history_text`, `failure_history_text`, `delivery_metadata` | Same history and delivery controls as `send_text`. |

Local artifact paths must be inside `ctx.input.workspace`,
`ctx.output.workspace`, or the matching `session_root`. Artifact deliveries that
write history should provide `history_text`; otherwise later model context has
no usable text representation.

### `progress` and `notice`

```python
ctx.output.progress("Still working...")
ctx.output.notice("Skipped optional indexing step.")
```

| Method | Meaning |
| --- | --- |
| `progress(text, visible=True, delivery_metadata=None)` | Emit transient progress. |
| `notice(text, visible=True, delivery_metadata=None)` | Emit a transient notice. |

`progress` and `notice` always use transient history. They do not accept
`history_policy`.

## History Client

Input and output slots can inspect current-session history:

```python
messages = ctx.history.recent_messages(5, roles={"user", "assistant"})
for message in messages:
    ctx.output.notice(f"{message.role}: {message.content[:80]}")
```

| API | Meaning |
| --- | --- |
| `ctx.history.recent_messages(limit, roles=None)` | Return recent `HistoryMessageSummary` items from the current session. |

`roles` defaults to `{"user", "assistant", "tool"}`. Other roles are ignored.
A non-positive `limit` returns an empty list.

`HistoryMessageSummary` has:

| Field | Meaning |
| --- | --- |
| `message_id` | Session message id. |
| `role` | `user`, `assistant`, or `tool`. |
| `content` | Stored message text. |
| `turn_id` | Turn that produced the message, when known. |
| `created_at` | Creation timestamp string. |
| `step_id` | Model/tool-loop step id, when available. |
| `tool_call_id` | Tool call id for tool result messages. |
| `tool_calls` | Assistant tool calls attached to assistant messages. |
| `visible` | Whether the message is user-visible. |
| `model_visible` | Whether later provider context can include the message. |
| `tool_name` | Tool name for tool result messages. |
| `is_error` | Tool error flag for tool result messages. |

## State Client

Input and output slots can use host-managed state:

```python
count = ctx.state.session.get("draft_count", 0)
ctx.state.session.set("draft_count", count + 1)
ctx.state.core.merge("preferences", {"tone": "concise"})
```

`ctx.state.core` is scoped to the Agent Core. `ctx.state.session` is scoped to
the current session.

| Method | Required capability | Meaning |
| --- | --- | --- |
| `get(target, default=None)` | `state.core.read` or `state.session.read` | Read one target. |
| `set(target, value)` | `state.core.write` or `state.session.write` | Replace one target. |
| `merge(target, value)` | Write capability | Merge an object into one target. |
| `append(target, value)` | Write capability | Append one value to a target. |
| `snapshot()` | Read capability | Return the full state snapshot for that scope. |

Target-specific grants such as `state.session.write:draft_count` can satisfy a
targeted operation when configured. Otherwise the generic scope capability is
required.

## Tools Client

Input and output slots can call visible tools:

```python
result = await ctx.tools.call("project_note", {"topic": "release"})
ctx.output.notice(result.content)
```

| API | Required capability | Meaning |
| --- | --- | --- |
| `await ctx.tools.call(name, arguments=None)` | `tool.call:<name>` | Execute a visible host, authored, or MCP tool. |

The tool must be visible to the current core and allowed by normal host
capability and approval policy.

## Child Agent Client

Input and output slots can run child agents:

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

| API | Required capability | Meaning |
| --- | --- | --- |
| `await ctx.agents.run(core_id, raw_input, ...)` | `agents.run:<core_id>` | Run a child turn and wait for `AgentRunResult`. |
| `ctx.agents.spawn(core_id, raw_input, ...)` | `agents.spawn:<core_id>` | Start an `agent.spawn` background task and return `AgentSpawnHandle`. |

Both calls accept:

| Parameter | Meaning |
| --- | --- |
| `context` | Extra string or list of strings injected into the child turn. |
| `input_slots` | `None`, `[]`, `"all"`, or a non-empty list of child input slot ids. |
| `output_slots` | `None`, `[]`, `"all"`, or a non-empty list of child output slot ids. |
| `use_bootstrap` | Defaults to `False`; `True` uses the child core bootstrap pipeline. |
| `tools` | `"all"`, `"none"`, `[]`, or a list of child tool ids. |

For `input_slots` and `output_slots`, omitted, `None`, or `[]` runs only
`base_input` or `base_output`. `"all"` runs the child core's full configured
pipeline. A non-empty list filters the active child pipeline by slot id.

For `tools`, omitted, `None`, or `"all"` uses the child core's configured
tools. `"none"` or `[]` hides all child tools. A non-empty list narrows the
child core's configured tools.

## Skills Client

Input slots can activate skills for the current turn:

```python
def process(ctx):
    ctx.skills.activate("release-checklist")
```

The slot needs `skill.activate` or `skill.activate:<skill>` capability. Unknown
skill names are ignored after the activation request is recorded.

## Result Client

Output slots can set a structured result for the current turn:

```python
def process(ctx):
    ctx.result.set({"summary": ctx.output.response_text[:200]})
```

| API | Meaning |
| --- | --- |
| `ctx.result.value` | Current result value, or `None` if unset. |
| `ctx.result.set(value)` | Set or merge a JSON-compatible result value. |

Parallel output slots cannot modify `ctx.result`. Result values must be
JSON-compatible. Floating point values must be finite.

## Capability Checks

Use `ctx.capability` when authored code is about to request a host-mediated
effect:

```python
def process(ctx):
    ctx.capability.require("fs.read", slot_path=ctx.slot_path)
```

| API | Meaning |
| --- | --- |
| `ctx.capability.can(capability, slot_path=ctx.slot_path)` | Return whether the capability is granted. |
| `ctx.capability.require(capability, slot_path=ctx.slot_path)` | Raise if the capability is not granted. |

Declaring a capability in `slot.yaml` makes the grant available to the slot.
It does not bypass host approval, workspace scope, command guards, channel
policy, or tool runtime rules.
