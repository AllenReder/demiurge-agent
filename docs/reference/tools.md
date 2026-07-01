# Tools Reference

Tools are executed by the host. Agent cores declare which built-in toolsets are
enabled and may add authored tools or MCP server tools.

## Built-In Toolsets

The default assistant enables:

```yaml
tools:
  toolsets:
    - coding
    - demiurge_control
    - schedule
```

`coding` includes file/search, terminal/process, web extract, skills, todo,
clarify, and session search tools.

`demiurge_control` includes `tools_list`, `evolve_core`, and `rollback_core`.

`schedule` includes `schedule_manage` for controlled management of authored
cron schedule YAML files in the active core.

## Built-In Tools

| Tool | Purpose |
| --- | --- |
| `read_file` | Read text inside the workspace. |
| `write_file` | Replace a workspace file. |
| `patch` | Apply an exact text replacement. |
| `search_files` | Search file contents or names. |
| `terminal` | Run a command inside the workspace. |
| `process` | Manage background processes started by `terminal`. |
| `web_extract` | Fetch and extract text from a URL. |
| `skills_list` | List skill metadata. |
| `skill_view` | Load a skill or linked skill file. |
| `skill_manage` | Create, update, or delete runtime-core skills. |
| `todo` | Maintain a per-session todo list. |
| `clarify` | Ask the user for needed input. |
| `session_search` | Search or browse local session messages. |
| `schedule_manage` | List, create, update, enable, disable, or delete core-authored schedule YAML. |
| `tools_list` | List tools visible to the active core. |
| `evolve_core` | Create, gate, and promote a candidate core. |
| `rollback_core` | Switch back to a previous stable core version. |

`schedule_manage` only manages cron expressions and prompts. Created schedules
use the existing schedule defaults: UTC, `base_input`, `base_output`, and local
delivery. It is not a Hermes-style runtime job store.

## Authored and MCP Tools

Authored tools live under `agent/tools/`. MCP tools are discovered from
`agent/mcp/*.yaml` and exposed as namespaced tool names such as
`docs__lookup`.

Both pass through the same host registry, capability checks, approval policy,
event logging, and output shaping.

## Removed Tool Names

| Old name | Replacement |
| --- | --- |
| `append_file` | `write_file` or `patch` |
| `delete_path` | No first-stage dangerous delete tool |

## Output Shaping

Tool results can include full structured `data`, model-facing summaries, and
display output. Event logs keep full data. Model context receives only
model-appropriate output.

TUI/Telegram display is controlled by:

```bash
uv run demiurge --tool-display quiet
uv run demiurge --tool-display summary
uv run demiurge --tool-display full
```

## Permissions

File writes, skill management, promptable terminal commands, network access,
evolution, and rollback require approval by default. Workspace escapes,
undeclared capabilities, and unknown tools are never allowed by config.
