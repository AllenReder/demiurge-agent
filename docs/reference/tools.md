---
title: Tools Reference
description: Reference for built-in, authored, and MCP tools.
---

# Tools Reference

The host builds the visible tool registry for each turn from:

- built-in toolsets in `agent.yaml`
- authored tools under `slots.tools`
- MCP tools discovered from `slots.mcp`

The Agent Core declares tool surfaces. The host owns selection, dispatch,
capability checks, approvals, workspace scope, task control, and result
conversion.

## Built-In Toolsets

| Toolset | Tools |
| --- | --- |
| `coding` | `read_file`, `write_file`, `patch`, `search_files`, `terminal`, `web_extract`, `skills_list`, `skill_view`, `skill_manage`, `todo`, `clarify`, `session_search` |
| `demiurge_control` | `tools_list`, `task_list`, `delegate_task`, `task_status`, `task_control`, `yield_until`, `evolve_core`, `rollback_core` |
| `schedule` | `schedule_manage` |

Unknown toolset names fail core loading.

## Built-In Tool Metadata

Built-in tools have host-defined risk, capability, and approval defaults. For
example:

| Tool | Capability | Default approval |
| --- | --- | --- |
| `read_file` | `fs.read` | `auto` for non-sensitive workspace reads; `prompt` outside workspace or for sensitive paths |
| `write_file` | `fs.write` | `prompt` |
| `patch` | `fs.write` | `prompt` |
| `terminal` | `terminal.exec` | `prompt` |
| `web_extract` | `network.fetch` | `prompt` |
| `schedule_manage` | `schedule.manage` | `prompt` |
| `evolve_core` | `tool.call:evolve_core` | `prompt` |
| `rollback_core` | `tool.call:rollback_core` | `prompt` |

Core metadata can make built-in tools stricter, but it cannot lower their risk
or weaken their approval policy.

## Authored Tools

Authored tools live under the root configured by `slots.tools`, usually:

```text
agent/tools/<tool_id>/
  tool.yaml
  module.py
```

If `slots.tools` is omitted, authored tools are not discovered.

Accepted `tool.yaml` fields are:

| Field | Default | Meaning |
| --- | --- | --- |
| `entrypoint` | `module:execute` | Callable loaded from the tool directory. |
| `description` | `""` | Model-visible tool description. |
| `input_schema` | `{}` | Model-visible JSON schema. |
| `risk` | `medium` | Registry risk metadata. |
| `capability` | `null` | Primary registry capability for this tool's approval metadata. |
| `approval_policy` | `prompt` | Tool-level approval metadata. |
| `display_policy` | `summary` | Operator display hint. |
| `model_output_policy` | `content` | Model-output conversion hint. |
| `capabilities` | `[]` | Capabilities the implementation may require through `ctx.capability.require(...)`. |

`tool.yaml` does not accept slot-only fields such as `failure_policy`,
`history_policy`, `default_placement`, or `timeout_seconds`.

The singular `capability` and the `capabilities` list are separate:

- `capability` identifies the tool in registry and approval metadata.
- `capabilities` grants effect capabilities to the tool implementation.

Authored tools are not listed in `agent/pipelines.yaml`.

## Authored Tool Runtime

The default entrypoint is:

```python
def execute(ctx, args):
    ...
```

The host passes a `ToolContext` with:

| Attribute | Meaning |
| --- | --- |
| `ctx.turn` | Current turn metadata. |
| `ctx.slot_id` | Tool id. |
| `ctx.slot_path` | Relative tool path, such as `agent/tools/project_note`. |
| `ctx.capability` | Capability facade for `can(...)` and `require(...)`. |
| `ctx.output` | Delivery client when the tool is called inside an active turn. |
| `ctx.workspace` | Resolved workspace root. |

Return `demiurge.sdk.ToolResult`, a compatible dict, or any value that can be
converted to text.

`ToolResult.content` is the default model-visible result. `model_output`
overrides what the model sees, and `display_output` overrides what operator UIs
and channels show in tool cards. For `terminal`, display output includes the
executed command and cwd before the exit code, stdout, and stderr; the
model-visible result keeps the existing exit/output shape.

## MCP Tools

MCP servers live under the configured MCP root, usually:

```text
agent/mcp/<server_id>.yaml
```

For each enabled server, the host:

1. Starts or connects to the server.
2. Lists server tools.
3. Applies `tools.include` and `tools.exclude`.
4. Builds safe names such as `docs__search_docs`.
5. Exposes those tools through the same registry as built-in and authored tools.

MCP tool calls require the server capability, defaulting to
`mcp.call:<server_id>` unless the server manifest sets `capability`.

## Tool Metadata Overrides

Use `agent.yaml`:

```yaml
tools:
  metadata:
    web_extract:
      approval_policy: deny
    project_note:
      risk: low
      enabled: false
```

Supported metadata keys are:

- `risk`
- `capability`
- `approval_policy`
- `model_output_policy`
- `display_policy`
- `enabled`

## Built-In Skill Tools

`skills_list` lists skill metadata. `skill_view(name)` loads a skill's
`SKILL.md`, and `skill_view(name, file_path)` loads linked files under
`references/`, `templates/`, `scripts/`, or `assets/`.

`skill_manage` writes skills in the active runtime core's configured skills
root. It supports:

- `create` and `update` for full `SKILL.md` writes.
- `patch` for `old_string` / `new_string` replacement in `SKILL.md` or a
  support file.
- `write_file` and `remove_file` for support files under `references/`,
  `templates/`, `scripts/`, or `assets/`.
- `delete` for removing a skill from the runtime core.

Every `skill_manage` write requires `fs.write` approval. The host rejects
absolute paths, parent traversal, hidden path segments, and writes outside the
configured skills root. Changes take effect for later turns; the current turn
does not hot-reload the active core.

## Core Evolution Tools

`evolve_core` is a single model-visible tool with four actions:

| Action | Required fields | Effect |
| --- | --- | --- |
| `start` | `goal` | Creates `.evolve/runs/<run_id>/agents` and runs the host-managed evolver. |
| `review` | `run_id` | Runs host-owned gates and writes `refs/demiurge/runs/<run_id>`. |
| `promote` | `run_id` | Reruns gates and advances `refs/demiurge/previous` and `refs/demiurge/live`. |
| `discard` | `run_id` | Removes the run worktree and metadata. |

`promote` is a high-risk operation and requires approval. `rollback_core`
creates a new rollback commit for the live Agent Core tree; the new revision
takes effect on the next turn.

## Child Agent Slot Controls

Authored slots can call child agents synchronously or in the background:

```python
result = await ctx.agents.run(
    "evolver",
    "child prompt",
    input_slots=["base_input"],
    output_slots=["base_output"],
    use_bootstrap=False,
)

handle = ctx.agents.spawn(
    "evolver",
    "child prompt",
    input_slots="all",
    output_slots="all",
    use_bootstrap=True,
)
```

`ctx.agents.run(...)` waits for the child turn and returns an
`AgentRunResult`. `ctx.agents.spawn(...)` returns an `AgentSpawnHandle` for an
`agent.spawn` background task.

`input_slots` and `output_slots` accept:

| Value | Meaning |
| --- | --- |
| omitted, `None`, or `[]` | Run only `base_input` or `base_output`. |
| `"all"` | Run the child core's full configured pipeline, including parallel slots. |
| non-empty list | Filter the child core's active pipeline by slot id, preserving pipeline order and serial/parallel grouping. |

Slot ids must exist and must already be present in the child core's active
pipeline. Invalid ids raise `ValueError` from authored `ctx.agents` calls.

`use_bootstrap` defaults to `False`. When false, the child turn does not run
bootstrap slots, create a bootstrap snapshot, or inject an existing bootstrap
snapshot into the provider request. Set `use_bootstrap=True` to use the child
core's normal bootstrap pipeline.

`delegate_task(...)` exposes the same child slot controls to the model:

```text
delegate_task(
  goal,
  core_id=None,
  context_mode="isolated",
  notify_policy="return_to_parent",
  tool_policy=None,
  max_depth=None,
  input_slots=["base_input"],
  output_slots=["base_output"],
  use_bootstrap=False,
)
```

For `delegate_task`, invalid child slot selection returns a tool error result
instead of raising into authored slot code.

## Background Runtime Tasks

These calls submit host-owned background tasks:

- `terminal(background=true)`
- `delegate_task(...)`
- `ctx.agents.spawn(...)`
- `evolve_core(action="start", background=true)`

Background task tools return a `task_id`. Use `task_status`,
`task_control(command="cancel")`, `yield_until`, or `task_list` to inspect or
control them. If `yield_until` returns a terminal or blocked status, that tool
result consumes the task's pending completion notification, so the same result
does not also trigger a separate background-completion turn. If `yield_until`
reaches its timeout while the task is still running, it returns the current task
status with `timed_out=true`; the timeout does not mean the task failed.
`task_list` is scoped to the current session.

Foreground `/stop` cancels only the foreground turn. It does not cancel
background tasks.

`agent.spawn` task metadata includes both the requested child slot controls and
the resolved child pipeline slots after the child turn runs. Use `task_status`
or `yield_until` to inspect those fields.

## Package-Provided Web Search

`web_search` is not part of the default `coding` toolset. It is installed by
provider packages such as `web_search_brave` or `web_search_tavily`.

Both packages expose the model-facing tool name `web_search`. Because both
packages target `agent/tools/web_search`, install only one web search provider
package in a core at a time.

`web_extract` remains the built-in tool for fetching a known URL.

## Inspect Visible Tools

Use the built-in tool:

```text
tools_list
```

Or use the TUI command:

```text
/tools
```

Tool display can be adjusted at startup:

```bash
uv run demiurge --tool-display quiet
uv run demiurge --tool-display summary
uv run demiurge --tool-display full
```
