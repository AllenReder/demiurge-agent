---
title: Tools Reference
description: Reference for built-in, authored, and MCP tools.
---

# Tools Reference

The host builds a visible tool registry from built-in toolsets, authored tools,
and MCP tools.

## Built-In Toolsets

| Toolset | Examples |
| --- | --- |
| `coding` | `read_file`, `write_file`, `patch`, `search_files`, `terminal`, `run_terminal`, `web_extract`, `skills_list`, `skill_view`, `skill_manage`, `todo`, `clarify`, `session_search`. |
| `demiurge_control` | `tools_list`, `task_list`, `delegate_task`, `task_status`, `task_control`, `yield_until`, `evolve_core`, `rollback_core`. |
| `schedule` | `schedule_manage`. |

## Authored Tools

Authored tools live under:

```text
agent/tools/<tool_id>/
```

They use `tool.yaml` plus a Python entrypoint, usually:

```yaml
entrypoint: module:execute
description: "Return project information."
capabilities: []
```

```python
def execute(ctx, args):
    ...
```

Authored tools are model-callable actions executed through the host tool
runtime. They are not Agent Slots and are not listed in `agent/pipelines.yaml`.

## Built-In Tools

| Tool | Purpose |
| --- | --- |
| `read_file` | Read text inside the workspace. |
| `write_file` | Replace a workspace file. |
| `patch` | Apply an exact text replacement. |
| `search_files` | Search file contents or names. |
| `terminal` | Run a command inside the workspace. |
| `run_terminal` | Run a command as a runtime task; defaults to background execution. |
| `web_extract` | Fetch and extract text from a known URL. |
| `skills_list` | List skill metadata. |
| `skill_view` | Load a skill or linked skill file. |
| `skill_manage` | Create, update, or delete runtime-core skills. |
| `todo` | Maintain a per-session todo list. |
| `clarify` | Ask the user for needed input. |
| `session_search` | Search or browse local session messages. |
| `schedule_manage` | Manage core-authored schedule YAML. |
| `tools_list` | List tools visible to the active core. |
| `task_list` | List controllable background tasks. |
| `delegate_task` | Spawn a child agent task. |
| `task_status` | Inspect a delegated task or runtime task. |
| `task_control` | Cancel a delegated task or background runtime task. |
| `yield_until` | Wait briefly for a delegated or background task. |
| `evolve_core` | Create, gate, and promote a candidate core through the host. |
| `rollback_core` | Switch back to a previous stable core version. |

`schedule_manage` creates schedules with explicit defaults for enabled state,
`base_input`, `base_output`, and local delivery. Runtime timezone belongs to the
host runtime, not to individual schedule YAML files.

## Background Runtime Tasks

`terminal(background=true)`, `run_terminal(...)`, `delegate_task(...)`,
`ctx.agents.spawn(...)`, and `evolve_core(background=true)` submit host-owned
background tasks. Background tool calls return a `task_id`.

`background=true` defaults to `notify_on_complete=true`. When a task completes,
the host records a pending completion event in SQLite and wakes any live channel
subscriber. If a user turn is already running, the completion waits. If user
input and completion are both pending, user input runs first and pending
completion summaries are merged into that user turn. `/stop` cancels only the
foreground turn; use `task_control(command="cancel", task_id="...")` to stop a
background task.

Delegation tools are the preferred model-facing controls for child agents.
`delegate_task` defaults to isolated context and `return_to_parent` notification;
child output is evidence for the parent and is not sent directly to the user.
Use `task_list`, `task_status`, `task_control`, and `yield_until` for follow-up
inspection or control. Operators can use `/subagents`, `/subagents <task_id>`, and
`/subagents cancel <task_id>` from TUI or Telegram to list, inspect, or cancel
child agent tasks for the current session.

The task tools are:

| Tool | Purpose |
| --- | --- |
| `task_list(kind=None, owner_session_id=None)` | List controllable background tasks. It never lists foreground `agent.turn` records. |
| `task_status(task_id, view="model")` | Return status, metadata, summary, and log tail for one task. |
| `yield_until(task_id, timeout_seconds=30)` | Wait briefly for task completion. |
| `task_control(task_id, command="cancel")` | Cancel a queued or running task. Other commands are rejected as unsupported. |

Task statuses are `queued`, `running`, `blocked_needs_user`, `succeeded`,
`failed`, `cancelled`, and `lost`. Completion payloads include metadata,
summary, result reference, and a bounded log tail.

Runtime control-plane projections are the durable source of truth for new task
state. Background tasks still declare a `write_scope`; another active task with
the same scope is rejected to avoid foreground/background or
background/background overwrite races.

## Package-Provided Web Search

`web_search` is not part of the default `coding` toolset. It is installed by
provider packages such as `web_search_brave` or `web_search_tavily`.

Both packages expose the same model-facing tool name, `web_search`, but own
provider-specific request code and config in separate libraries. Because both
packages target `agent/tools/web_search`, only one web search provider package
can be installed in a core at a time.

`web_extract` remains the built-in tool for fetching a known URL.

## MCP Tools

MCP tools come from declarations under:

```text
agent/mcp/*.yaml
```

The host namespaces and filters MCP tools, then runs them through capability and
approval policy.

## Output Policy

Tool results can be model-visible, current-turn-only, or shaped by tool metadata
depending on the registry entry. Tool runtime owns conversion to provider
messages.

TUI and gateway display can be controlled with:

```bash
uv run demiurge --tool-display quiet
uv run demiurge --tool-display summary
uv run demiurge --tool-display full
```

## Boundary

Agent Cores can declare authored tools and MCP servers. The host owns visible
tool selection, dispatch, approval, workspace checks, result conversion, and
tool-call replay.
