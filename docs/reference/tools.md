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
| `coding` | `read_file`, `write_file`, `patch`, `search_files`, `terminal`, `process`, `web_extract`, `skills_list`, `skill_view`, `skill_manage`, `todo`, `clarify`, `session_search`. |
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
| `process` | Manage background processes started by `terminal`. |
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
