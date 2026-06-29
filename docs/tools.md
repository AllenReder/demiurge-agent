# Tools

Tools are executed by the host. Agent cores only declare which toolsets are
enabled. Tool execution passes through capability checks, workspace scope,
approval policy, and event logging.

The default `assistant` core enables:

```yaml
tools:
  toolsets:
    - coding
    - demiurge_control
```

The old `tools.allow_builtin` entry has been removed and has no compatibility
alias.

## Built-In Toolsets

File and search tools:

- `read_file`: read text inside the workspace, with offset/limit support.
- `write_file`: overwrite a text file inside the workspace and create parent
  directories when needed.
- `patch`: apply exact text replacements and return a unified diff.
- `search_files`: search file contents or names with
  `target="content|name|both"` and `pattern`.

Terminal and process tools:

- `terminal`: run a shell command inside the workspace. `background=true`
  starts a background process and returns a `process_id`.
- `process`: manage background processes created by `terminal(background=true)`.
  Supports `list`, `poll`, `log`, `wait`, and `kill`. The process registry is
  in-memory for the current demiurge process and is not restored after restart.

Network:

- `web_extract`: fetch a URL and extract text. Results are truncated by
  `max_chars` before entering model context.

Skills:

- `skills_list`: return lightweight metadata for `agent/skills/`.
- `skill_view`: load a skill main document or packaged linked file into the
  current turn context. This does not grant new permissions.
- `skill_manage`: create, update, or delete skills under the current runtime
  core's `agent/skills/`. Writes require approval and are logged.

General agent tools:

- `todo`: maintain a per-session todo list.
- `clarify`: end the current turn and ask the user for input. TUI opens a
  prompt modal; Telegram sends numbered choices and inline buttons.
- `session_search`: read-only search or browse existing session messages. It
  currently reads `messages.jsonl` and does not use SQLite/FTS.

Control tools:

- `tools_list`: return visible tools for the current agent.
- `evolve_core`: create a candidate, apply structured file operations through
  host `EvolutionRuntime`, run gates, and promote on success.
- `rollback_core`: request rollback to the previous stable version or a
  specified version.

## Removed Tool Names

These old public tool names are no longer available:

| Old Name | Replacement |
| --- | --- |
| `append_file` | `write_file` or `patch` |
| `delete_path` | no first-stage dangerous delete tool |
| `web_search` | no built-in replacement; default core exposes `web_extract` |

## Registry Metadata

The host keeps one registry entry for each visible tool:

- `source`: `builtin` or `authored`;
- schema and model-facing description;
- required capabilities;
- risk metadata;
- approval defaults;
- output shaping metadata.

TUI `/tools`, model `tools_list`, event logs, and provider tool definitions are
derived from the same registry so UI and model boundaries stay consistent.

Tool schema descriptions should explain when to use the tool, parameter
semantics, output shape, and tradeoffs. Approval and risk details are runtime
metadata; they are visible to UI and logs but should not pollute model-facing
capability descriptions.

Authored tools can call `ctx.output.send_text(...)` and media/file `send_*`
methods to deliver user-visible output through the host. These deliveries use
the same artifact registration, channel routing, and `history_policy` behavior
as input/output modules. The returned `ToolResult` remains the model-facing
tool result and is written as a hidden, model-visible `role="tool"` transcript
message.

## Output Shaping

Tools can return full structured `data`, truncated `model_output`, and separate
`display_output`. Event logs keep full data. Session transcript and model
context receive only model-appropriate summaries.

Tool results that participate in the model loop are written to session history
as `visible=false, model_visible=true` transcript messages.

TUI/Telegram display is a separate layer:

- `--tool-display quiet`: show final assistant messages only.
- `--tool-display summary`: default, show compact tool summaries.
- `--tool-display full`: show arguments, full results, and `model_output`.

TUI can switch at runtime with `/tool-display quiet|summary|full`. Telegram
currently resolves the setting at channel startup.

## Permissions

An agent core must enable the toolset and declare matching capabilities. File
writes, skill management, terminal commands, and network access require approval
by default. Ordinary read-only file access inside the workspace may be allowed
automatically, but sensitive reads still require approval.
