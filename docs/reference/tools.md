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
| `coding` | `read_file`, `write_file`, `patch`, `search_files`, `terminal`, `job`, `process`, `web_extract`, `skills_list`, `skill_view`, `skill_manage`, `todo`, `clarify`, `session_search`. |
| `demiurge_control` | `tools_list`, `evolve_core`, `rollback_core`. |
| `schedule` | `schedule_manage`. |

## Authored Tools

Authored tools live under:

```text
agent/tools/<tool_id>/
```

They use `slot.yaml` plus a Python entrypoint, usually:

```yaml
entrypoint: module:execute
```

```python
def execute(ctx, args):
    ...
```

## Built-In Tools

| Tool | Purpose |
| --- | --- |
| `read_file` | Read text inside the workspace. |
| `write_file` | Replace a workspace file. |
| `patch` | Apply an exact text replacement. |
| `search_files` | Search file contents or names. |
| `terminal` | Run a command inside the workspace. |
| `job` | Manage background jobs. |
| `process` | Compatibility view for terminal background jobs. Prefer `job`. |
| `web_extract` | Fetch and extract text from a known URL. |
| `skills_list` | List skill metadata. |
| `skill_view` | Load a skill or linked skill file. |
| `skill_manage` | Create, update, or delete runtime-core skills. |
| `todo` | Maintain a per-session todo list. |
| `clarify` | Ask the user for needed input. |
| `session_search` | Search or browse local session messages. |
| `schedule_manage` | Manage core-authored schedule YAML. |
| `tools_list` | List tools visible to the active core. |
| `evolve_core` | Create, gate, and promote a candidate core through the host. |
| `rollback_core` | Switch back to a previous stable core version. |

`schedule_manage` creates schedules with explicit defaults for enabled state,
`base_input`, `base_output`, and local delivery. Runtime timezone belongs to the
host runtime, not to individual schedule YAML files.

## Background Jobs

`terminal(background=true)`, `ctx.agents.spawn(...)`, and
`evolve_core(background=true)` submit host-owned in-memory jobs. Background
tool calls return a `job_id`; terminal calls also return `process_id` as a
compatibility alias.

`background=true` defaults to `notify_on_complete=true`. When a job completes,
the host queues a synthetic model turn in the originating session. If a user
turn is already running, the completion waits. If user input and completion are
both pending, user input runs first and pending completion summaries are merged
into that user turn. `/stop` cancels only the foreground turn; use
`job(action="cancel", job_id="...")` to stop a background job.

The `job` tool supports:

| Action | Purpose |
| --- | --- |
| `list` | List jobs, optionally filtered by `backend` or `owner_session_id`. |
| `poll` | Return status, metadata, summary, and log tail for one job. |
| `log` | Return the in-memory job log. Use `tail` to limit lines. |
| `wait` | Wait up to `timeout_seconds` for completion. |
| `cancel` | Cancel a queued or running job. |

Job statuses are `queued`, `running`, `blocked_needs_user`, `succeeded`,
`failed`, `cancelled`, and `lost`. Completion payloads include metadata,
summary, result reference, and a bounded log tail; full in-memory logs are
available through `job(action="log")`.

The first implementation is in-memory only. Running jobs, logs, and pending
completion events are lost when the host process exits. Jobs declare a
`write_scope`; another active background job with the same scope is rejected to
avoid foreground/background or background/background overwrite races.

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
