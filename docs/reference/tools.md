---
title: Tools Reference
description: Reference for built-in, authored, and MCP tools.
---

# Tools Reference

The host builds the visible tool registry for each turn from:

- built-in toolsets in `agent.yaml`
- authored tools under `slots.tools`
- MCP tools discovered from `slots.mcp`

The Agent Core declares tool surfaces. The Host is the product owner for
selection, dispatch, capability checks, approvals, workspace scope, task
control, and result conversion. One per-turn resolved catalog now produces the
provider definitions, `tools_list` display, effective approval metadata, and
the exact adapter-bound `EffectRequest` used by dispatch. Builtin, authored,
and MCP calls no longer perform a second source lookup by global tool name.
MCP connect/discovery now has its own `mcp.connect:<server>` capability and
approval gate before client construction; a later call uses the separate
connection-bound `mcp.call:<server>` path.

## Built-In Toolsets

| Toolset | Tools |
| --- | --- |
| `coding` | `read_file`, `write_file`, `patch`, `search_files`, `terminal`, `web_extract`, `skills_list`, `skill_view`, `skill_manage`, `todo`, `clarify`, `session_search` |
| `demiurge_control` | `tools_list`, `task_list`, `delegate_task`, `task_status`, `task_control`, `yield_until`, `evolve_core`, `rollback_core` |
| `schedule` | `schedule_manage` |

Unknown toolset names fail core loading.

## Tool Name Uniqueness

Model-visible tool names must be unique across builtin, authored, and MCP
sources. A builtin/authored collision fails core loading. A collision involving
a discovered MCP tool fails final catalog construction. The diagnostic includes
both provenances and requires a rename; the Host never silently prefers the
builtin implementation. This is an intentional alpha breaking change for cores
that previously relied on ambiguous names.

## Built-In Tool Metadata

Built-in tools have host-defined risk, capability, and approval defaults. For
example:

| Tool | Capability | Registry approval metadata |
| --- | --- | --- |
| `read_file` | `fs.read` | `auto` for non-sensitive workspace reads; `prompt` outside workspace or for sensitive paths |
| `write_file` | `fs.write` | `prompt` |
| `patch` | `fs.write` | `prompt` |
| `terminal` | `terminal.exec` | `prompt` |
| `web_extract` | `network.fetch` | `prompt` |
| `schedule_manage` | `schedule.manage` | `prompt` |
| `evolve_core` | `tool.call:evolve_core` | `prompt` |
| `rollback_core` | `tool.call:rollback_core` | `prompt` |

Before terminal approval, the Host lexically reviews the execution-faithful raw
command and additional ANSI-stripped/NFKC detection candidates. Normalization
adds checks; it never replaces the raw shell interpretation. Only recognized
literal commands classified `allow/low` can use automatic approval. Command
substitution (`$()` and backticks), process substitution, parameter/arithmetic
expansion (including legacy `$[...]`), malformed shell forms, and unknown
commands remain `prompt/high`; a global `auto` fallback cannot lower that
command-guard decision. Known destructive payloads are blocked before the
approval provider is called. Single-quoted or escaped metacharacters remain
literal when the scanner can prove that interpretation.

This lexical guard is containment, not a shell sandbox or a complete shell AST.
An explicitly approved command still runs through the Host terminal runtime.
Ambiguous shell approvals use a fingerprint of the command, cwd, explicit
environment overlay, and execution options such as foreground/background mode
and timeout. One approval therefore does not authorize a different execution
shape through the same coarse rule key. This fingerprint does not replace the
separate session/principal ownership contract. Ambiguous shell text, including
expansion syntax inside comments, can conservatively require approval.

### Terminal environment and secret bindings

Terminal subprocesses are built from an environment allowlist rather than
`os.environ`. The Host preserves basic execution/locale/temp variables, sets
`HOME` to a dedicated runtime directory, applies the configured timezone, and
omits provider, channel, MCP, cloud, and desktop credentials. Explicit `env`
overlays require approval and are included in the command fingerprint by key
and value; approval/event views expose keys only.

Commands such as `pytest`, `python -m pytest`, `uv run`, `npm run`, `cargo
test/build`, and `make` can execute repository code, plugins, or build scripts,
so they are `prompt/high` rather than unconditional safe commands. Literal
read-only commands remain eligible for automatic approval. Execution-capable
forms such as `rg --pre`, `find -exec`, GNU `sed` `e`, and Git external
diff/textconv/pager options are also `prompt/high`. Git auto-approval is limited
to explicit read-only shapes; remote network operations and branch, tag, remote,
or worktree mutations require approval, as do `--output` file writes. Common
embedded write modes in otherwise read-oriented commands and inline environment
assignments also require approval; system-time mutation is never treated as a
literal read. Auto-approved executable names must be bare commands rather than
workspace-relative paths. Wrapper or shell cwd changes, command files, embedded
read-path options, and unquoted filename expansion require approval. Common
credential paths such as `.npmrc`, `.pypirc`, `.netrc`, `.aws/`, `.kube/`,
`.ssh/`, and `.env*` are sensitive defense-in-depth paths.

`terminal.secret_bindings` is an array of objects with `source` (`env:<NAME>`),
optional `target`, and optional `expires_in_seconds`. Each source requires the
exact `secret.bind:<NAME>` capability. Bindings are foreground-only, cannot
outlive the terminal timeout, never reuse a session approval, and are removed
from the Host-side environment after completion. Approval/audit views record
source, target, capability, expiry, actual cwd, environment keys, the resolved
shell/process executable, and the best-effort command executable without
values. Exact bound values in
stdout/stderr are replaced with `<redacted:TARGET>`.

Wildcard grants such as `secret.bind:*` are rejected. Binding targets also
cannot replace execution-control variables such as `PATH`, `HOME`, `COMSPEC`,
loader injection variables, language runtime search paths, or option hooks.
The earliest binding expiry clamps the foreground subprocess timeout; expiry
therefore terminates the owned process tree even when the requested command
timeout is longer.

### Terminal process and output lifecycle

Foreground and background terminal calls start under one Host-owned process
lifecycle and both are registered with Host shutdown. POSIX starts a new
session/process group, sends TERM, waits for a short grace deadline, then sends
KILL. Windows creates the process suspended, assigns a kill-on-close Job Object,
then resumes it. Timeout, foreground turn cancellation,
`task_control(command="cancel")`, and Host shutdown terminate the owned tree.
Cancellation is single-flight, and its terminal state is persisted before its
completion notification. Drain or task-log persistence failure also triggers
tree cleanup before a failed task is published.

The Host records the PID, process-group id, platform, a unique `spawn_id`, and
an OS process-start marker. Live cancellation closes over that process handle
and revalidates the marker before PID/PGID fallback termination rather than
trusting a caller-supplied or stale PID. This covers descendants that stay
in the owned OS process tree; it is not a hardened sandbox against approved
`host_shared` code that deliberately creates a new session or otherwise
escapes the platform tree boundary.

Stdout and stderr are drained continuously. Foreground results retain at most
12,000 characters per stream as a tail plus total byte/character and truncation
metadata; they do not first materialize complete output in memory. Background
terminal output is drained in bounded chunks into `task_logs`, with the same
bounded tail statistics stored in operator/debug task metadata. In parallel,
full streams are written incrementally to private durable terminal artifacts
and registered in the runtime artifact projection; a background task's
`result_ref` points to that artifact. Exact bound secrets are redacted before
artifact persistence. The artifact descriptor contains an opaque `root` and
stream-relative paths; the Host derives that root from session identity and
enforces containment below `runtime/artifacts`. If artifact open, write, flush,
or sync fails, the operation fails after continuing to drain the child pipes.
Durable log retention remains a separate runtime-store policy.

This filtering and redaction are not OS isolation. An approved command still
runs through the Host shell, and transformed/encoded secret output is outside
the exact-value redactor's guarantee.

For builtin handlers that use approval resolution, core metadata can make the
effective policy stricter but cannot lower risk or weaken the registry policy.
This includes every `evolve_core` action and `rollback_core`.

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
| `risk` | `medium` | Registry risk used by authored approval resolution. |
| `capability` | `null` | Primary registry capability required before module import when non-null. |
| `approval_policy` | `prompt` | Tool-level policy resolved before module import and invocation. |
| `display_policy` | `summary` | Operator display hint. |
| `model_output_policy` | `content` | Model-output conversion hint. |
| `capabilities` | `[]` | Capabilities the implementation may require through `ctx.capability.require(...)`. |

`tool.yaml` does not accept slot-only fields such as `failure_policy`,
`history_policy`, `default_placement`, or `timeout_seconds`.

The singular `capability` and the `capabilities` list are separate:

- `capability` identifies the tool in registry and approval metadata and must
  be granted by core defaults or path-scoped Host capability configuration.
- `capabilities` grants effect capabilities to the tool implementation.

The plural list cannot satisfy the singular dispatcher gate; an authored tool
cannot authorize its own invocation by repeating the singular value there.

The authored dispatcher resolves the same registry entry used for definitions,
requires its singular `capability` when present, then applies its `risk` and
`approval_policy` plus stricter core/global approval policy before importing and
calling the entrypoint. Approval requests use a bounded, field-name-redacted
argument preview rather than raw arguments. The `capabilities` list remains
separate and is enforced when authored code or an SDK client calls
`ctx.capability.require(...)`.
Because `host_shared` Python can also call ordinary Python/OS APIs directly,
these declarations are not a sandbox.

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

For error results, `executionStarted`, `denial`, and `approval` are reserved
Host lifecycle fields. Authored values for those keys are ignored: once the
entrypoint is invoked, the Host records `executionStarted: true` and derives
the typed effect status from its own capability, approval, and dispatch state.

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

1. Requires `mcp.connect:<server_id>` and resolves connect approval.
2. Starts or connects to the server only when connect authority allows it.
3. Lists server tools.
4. Applies `tools.include` and `tools.exclude`.
5. Builds safe names such as `docs__search_docs`.
6. Exposes those tools through the same registry as built-in and authored tools.

MCP tool calls then require the separate server call capability, defaulting to
`mcp.call:<server_id>` unless the server manifest sets `capability`.

Current behavior bounds `list_tools()` by `connect_timeout_seconds`; a timeout
closes that server connection, records a diagnostic, and continues to later
servers. Discovery uses one runtime-wide limit of four concurrent server
operations across sessions and preserves deterministic naming. Discovery
failure diagnostics use a per-server 30-second negative-cache TTL; within one
catalog authority, expiry retries only that server and preserves healthy peer
connections. Connect denial is rechecked per server on the next turn.
Per-server manifest fingerprints support targeted reconnects only while the
overall authority/core snapshot is unchanged. Catalog identity also binds
principal, capability snapshot, core revision, and effective connect policy;
changes to those bindings evict the whole stale catalog. Configured cwd must resolve inside the Host workspace before
approval/client construction.
Declaration changes close the older connection and require connect reapproval
before a replacement client starts. Removing all declarations closes remaining
connections. Starting or resuming another session tracks eviction of the
previous session, while explicit eviction closes only the selected session's
catalogs. Delegated children use their Host-issued authority and close MCP
connections when the child run ends. Connect approval previews expose safe
launch metadata without environment, header, URL credential/query, or argument
secret values. Stdio children use the shared allowlisted environment plus only
manifest-declared env entries after connect approval. URL policy remains
separate later security work.

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

Every action is classified as high risk with registry `prompt` policy.
When MCP declarations change, review records the exact changed declaration
paths plus a secret-safe before/after security summary and content-bound
`mcp-review:<sha256>` token. The model-visible promote path binds that token to
the normal promote approval; CLI/TUI callers must return the exact printed
token. Missing or stale confirmation does not move Git refs.
`evolve_core` requires its resolved capability and approval before foreground
adapter calls or background task creation; `rollback_core` does the same before
calling the version store. Approval rules are action-scoped, so a cached
allow for `promote` does not authorize `discard`, `review`, `start`, or
rollback. Session allows are bound to the Host-issued principal, session, core,
effective policy, and capability/core snapshot fingerprint. They expire after a
bounded TTL and are invalidated when the owner is revoked, the session is
replaced, the core changes, or the app closes. `rollback_core` creates a new
rollback commit for the live Agent Core tree; the new revision takes effect on
the next turn.

## Child Agent Controls

Authored slots can call child agents synchronously or in the background:

```python
result = await ctx.agents.run(
    "evolver",
    "child prompt",
    input_slots=["base_input"],
    output_slots=["base_output"],
    tools="all",
    use_bootstrap=False,
)

handle = ctx.agents.spawn(
    "evolver",
    "child prompt",
    input_slots="all",
    output_slots="all",
    tools=["tools_list"],
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

`tools` controls the child turn's visible and executable tool set:

| Value | Meaning |
| --- | --- |
| omitted, `None`, or `"all"` | Use the child core's configured tools. |
| `"none"` or `[]` | Run the child turn without tools. |
| non-empty list | Allow only the listed tool ids from the child core's configured tools. |

Tool selection only narrows the child core's configured tools; it does not grant
missing tools or capability grants. Builtin, authored, and MCP call policy still
applies in a child turn. Invalid tool ids raise `ValueError` from authored
`ctx.agents` calls.

`use_bootstrap` defaults to `False`. When false, the child turn does not run
bootstrap slots, create a bootstrap snapshot, or inject an existing bootstrap
snapshot into the provider request. Set `use_bootstrap=True` to use the child
core's normal bootstrap pipeline.

`delegate_task(...)` exposes the same child controls to the model:

```text
delegate_task(
  goal,
  core_id=None,
  context_mode="isolated",
  notify_policy="return_to_parent",
  max_depth=None,
  tools="all",
  input_slots=["base_input"],
  output_slots=["base_output"],
  use_bootstrap=False,
)
```

For `delegate_task`, invalid child slot or tool selection returns a tool error
result instead of raising into authored slot code.

## Background Runtime Tasks

These calls submit host-owned background tasks:

- `terminal(background=true)`
- `delegate_task(...)`
- `ctx.agents.spawn(...)`
- `evolve_core(action="start", background=true)`

Background task tools return a `task_id`. Use `task_status`,
`task_control(command="cancel")`, `yield_until`, or `task_list` to inspect or
control them. Detail, wait, completion consumption, and cancel are restricted
by the admitted `PrincipalScope`; an unowned id is indistinguishable from a
missing id. These model-facing tools return bounded status/result fields only;
they do not accept operator/debug views or return task logs. Full log inspection
uses a separate Host/operator surface. Model task payloads omit owner ids,
write scope, arbitrary metadata, and result references, and bound the summary.
`task_list` uses the same projection and remains restricted to the current turn
session. `/subagents` uses the same owner checks.
If `yield_until`
returns a terminal or blocked status, that tool result consumes the task's
pending completion notification, so the same result
does not also trigger a separate background-completion turn. If `yield_until`
reaches its timeout while the task is still running, it returns the current task
status with `timed_out=true`; the timeout does not mean the task failed.
`task_list` is scoped to the current session.

Cancelling a background terminal task first seals its in-memory state as
cancelled, terminates the owned process tree, records return code and exit
reason in the durable terminal event, and only then emits completion-ready.
Closing the Host applies the same cancellation path to active runtime tasks.

## Session History Search

`session_search` requires the `session.read` capability and resolved
`prompt/medium` approval before reading history. Its explicit-session, browse,
and full-text paths all use owner-scoped `SessionRuntime` queries. Ordinary
channel authority can read only the currently bound session. The local operator
may cross sessions only through its explicit audited operator scope, and a
missing or unauthorized session id produces the same external error.
Ambiguous `legacy_local` sessions remain hidden from normal owned queries and
are available only to the dedicated, reasoned, durably audited Host/operator
repair/status path.

Foreground `/stop` cancels only the foreground turn. It does not cancel
background tasks.

`agent.spawn` task metadata includes both the requested child slot controls and
the resolved child pipeline slots after the child turn runs. This operator-only
metadata is intentionally absent from model task payloads; inspect it through
the owner-checked `/subagents <task_id>` Host/operator detail surface.

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
