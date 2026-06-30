# Authoring Agent Cores

This page is for users who want to hand-author or customize an agent core. The
safest path is to modify a runtime core after initialization:

```text
~/.demiurge/agents/assistant/
  agent.yaml
  agent/
    SOUL.md
    bootstrap/  # optional session-start context modules
    input/
    output/
    tools/
    skills/      # optional; the default assistant starts empty
    schedules/   # optional cron declarations
    mcp/         # optional MCP server declarations
```

The repository-level `agents/assistant/` directory is the source template.
Normal startup fills in missing runtime files without overwriting local runtime
edits. An explicit `uv run demiurge init` backs up the existing runtime core
and refreshes it from the source template. Before hand-editing a core, either
back up `~/.demiurge/agents/assistant` or manage it with git.

## Boundary

An agent core provides the authored surface. The host still owns sessions,
turns, steps, context assembly, provider requests, tool execution, approvals,
state, history, delivery, and TUI/Telegram channels.

Do not call channel SDKs or host internals directly from authored modules:

- Do not call Telegram or TUI SDKs directly.
- Do not write `messages.jsonl`, `events.jsonl`, or provider messages directly.
- Do not send model requests directly.
- Do not bypass host-injected clients such as `ctx.state`, `ctx.tools`,
  `ctx.bootstrap`, `ctx.input`, or `ctx.output`.

Every core must keep these files:

- `agent/input/pipeline.yaml`
- `agent/output/pipeline.yaml`

Pipeline files define module order and concurrency. Module directory names do
not have special `base` or `main` privileges. The default template uses names
such as `base_input` and `base_output` by convention only.

`agent/bootstrap/` is optional. If it exists, it must contain
`agent/bootstrap/pipeline.yaml`. Bootstrap modules run once before the first
model request in a session, write a session snapshot to
`bootstrap_context.md`, and are reused on resume.

`agent/schedules/*.yaml` files are optional cron declarations. The host-owned
scheduler starts a fresh session for each trigger and runs one normal turn with
the input/output module lists declared by that schedule.

`agent/mcp/*.yaml` files are optional MCP server declarations. They belong to
the concrete core because they change the tools visible to that core. The host
owns MCP transports, subprocesses, logs, session state, and tool execution.

The model-step budget for a single turn belongs in the concrete core
`agent.yaml`:

```yaml
runtime:
  max_model_steps: 90
```

The default and host hard limit are both `90`; the supported range is `1..90`.
Do not put this field in the root `agents/agent.yaml` fallback file.

## Bootstrap Modules

Bootstrap modules build session-stable system context. They are useful for
context that should be fixed for a session instead of recalculated by input
modules on every turn. The host runs the bootstrap serial pipeline before the
first input pipeline in a session.

Create the module directory:

```bash
mkdir -p ~/.demiurge/agents/assistant/agent/bootstrap
mkdir -p ~/.demiurge/agents/assistant/agent/bootstrap/session_context
```

`~/.demiurge/agents/assistant/agent/bootstrap/pipeline.yaml`:

```yaml
serial:
  - session_context
```

Bootstrap pipelines are serial-only. They do not support `parallel`.

`~/.demiurge/agents/assistant/agent/bootstrap/session_context/slot.yaml`:

```yaml
entrypoint: module:process
description: "Adds session-stable context."
failure_policy: soft
capabilities: []
```

`~/.demiurge/agents/assistant/agent/bootstrap/session_context/module.py`:

```python
def process(ctx):
    ctx.bootstrap.add("Use this session context on every model request.")
```

The host joins successful module additions with blank lines and writes the
exact generated context to
`~/.demiurge/sessions/<session_id>/bootstrap_context.md`. It does not add
headers, module markers, timestamps, or metadata. An empty generated context
still creates an empty file so the session does not rerun bootstrap.

`ctx.bootstrap.add(text)` only appends system context. It does not write
`messages.jsonl`, does not create a user or assistant message, and does not
expose delivery methods. Authored modules are trusted Python code in the
host-shared environment, but dangerous effects should still go through
host-owned capabilities and APIs rather than bypassing the harness.

If a bootstrap module fails with `failure_policy: soft`, the host records the
failure event and continues without that module's additions. With
`failure_policy: hard`, the first model request is blocked and no bootstrap
snapshot is written.

## Input Modules

Input modules run before the model request. The host does not automatically add
channel input to the prompt. A module must explicitly append `user` or `system`
content to the input builder. Serial modules run in pipeline order, and prompt
content is appended in that same order.

Create the module directory:

```bash
mkdir -p ~/.demiurge/agents/assistant/agent/input
mkdir -p ~/.demiurge/agents/assistant/agent/input/mobile_hint
```

`~/.demiurge/agents/assistant/agent/input/pipeline.yaml`:

```yaml
serial:
  - base_input
  - mobile_hint
parallel: []
```

`~/.demiurge/agents/assistant/agent/input/mobile_hint/slot.yaml`:

```yaml
entrypoint: module:process
description: "Adds a short-answer hint for mobile Telegram conversations."
failure_policy: soft
history_policy: transient
capabilities: []
```

`~/.demiurge/agents/assistant/agent/input/mobile_hint/module.py`:

```python
def process(ctx):
    if ctx.input.raw_input.metadata.get("channel") != "telegram":
        return
    ctx.input.add(
        "system",
        "The user is on Telegram. Prefer concise, scannable replies.",
    )
```

`system` content only enters the current model request. It is not written to
session history. Use authorized `ctx.state` APIs for information that must
persist across turns.

A standard raw-input passthrough module usually looks like this:

```python
def process(ctx):
    ctx.input.add("user", ctx.input.raw_input.text)
```

If a `user` fragment should affect only the current prompt and should not be
written to session history, mark it transient:

```python
ctx.input.add("user", "Temporary routing hint", history_policy="transient")
```

Input modules may also emit pre-model delivery with
`ctx.input.send_text(...)`, `ctx.input.progress(...)`, or media/file `send_*`
methods. The host records history, events, and artifacts at the call site, and
the `delivery` argument controls when the item is sent to the channel.

When a schedule run enters input modules, `ctx.input.raw_input.text` is the
schedule `prompt`. Metadata includes `trigger: "schedule"`, `schedule_id`,
`run_id`, `due_at`, and `scheduled_at`.

## Output Modules

Output modules run after the model has finished. The host does not
automatically deliver model output. A module must explicitly call
`ctx.output.send_*`. `ctx.output.content` is the read-only model output.

Create the module directory:

```bash
mkdir -p ~/.demiurge/agents/assistant/agent/output
mkdir -p ~/.demiurge/agents/assistant/agent/output/debug_immediate_async
```

`~/.demiurge/agents/assistant/agent/output/pipeline.yaml`:

```yaml
serial:
  - base_output
parallel:
  - debug_immediate_async
```

`~/.demiurge/agents/assistant/agent/output/debug_immediate_async/slot.yaml`:

```yaml
entrypoint: module:process
description: "Debug async output with immediate progress."
failure_policy: soft
history_policy: transient
capabilities: []
```

`~/.demiurge/agents/assistant/agent/output/debug_immediate_async/module.py`:

```python
import asyncio


async def process(ctx):
    ctx.output.progress("[debug_immediate_async] started")
    await asyncio.sleep(3)
    ctx.output.notice("[debug_immediate_async] still working")
    ctx.output.send_text(
        "[debug_immediate_async] visible but model-hidden note",
        history_policy="model_hidden",
        delivery="immediate",
    )
```

`progress()` and `notice()` always use
`history_policy="transient", delivery="immediate"`. The final
`send_text(..., delivery="immediate")` is queued immediately, and
`history_policy="model_hidden"` means it is visible in session history but is
not included in later model context.

A standard output module usually looks like this:

```python
def process(ctx):
    ctx.output.send_text(ctx.output.content, history_policy="persist")
```

If no output pipeline module calls `ctx.output.send_*`, the final model output
for that turn is not delivered and is not written to session history. Assistant
tool-call steps and tool results are still written by the host as transcript
entries for later model context.

Input/output code slots are trusted authored modules.
`ctx.input/ctx.output.send_*` is a built-in phase capability and does not need
an extra delivery capability declaration. Higher-risk host APIs such as tool
calls, state writes, agent calls, evolution, and rollback still require their
own capabilities.

Authored tools also receive `ctx.output.send_text(...)`, media/file `send_*`,
`progress(...)`, and `notice(...)`. In a tool, `ctx.output` is delivery-only and
does not expose `content`; `ctx.output.content` belongs only to output modules.
If a tool `send_*` call omits `history_policy`, the host uses that tool slot's
`slot.yaml` default. The tool's `ToolResult` is still written separately as a
model-visible `role="tool"` transcript entry, even when a delivery uses
`history_policy="transient"`.

Each slot directory is loaded as an isolated package. `module.py` may use
private helpers in the same directory, but should import them relatively:

```python
from .helper import build_payload
```

Avoid bare imports such as `import helper`. Different slots may use the same
helper filename, and bare imports can be affected by Python's global module
cache.

Shared authored code for a core belongs under `agent/lib/`. Slot packages also
include that directory on their package path, so output modules, input modules,
and authored tools can use relative imports for shared helpers:

```python
from .tts_minimax.synthesizer import synthesize_to_file
```

Keep `agent/lib/` code behind the slot or tool APIs that call it. The host still
owns provider calls, tool scheduling, capabilities, state, sessions, and
delivery boundaries.

## MCP Servers

MCP servers are declared as YAML files under the active core's `agent/mcp/`
directory. Missing or empty `agent/mcp/` means no MCP tools are exposed.

`~/.demiurge/agents/assistant/agent/mcp/docs.yaml`:

```yaml
enabled: true
transport: stdio
command: npx
args:
  - -y
  - "@modelcontextprotocol/server-filesystem"
  - .
env: {}
cwd: null

tools:
  include: []
  exclude: []

risk: medium
approval_policy: prompt
capability: null
connect_timeout_seconds: 30
timeout_seconds: 60
supports_parallel_tool_calls: false
```

For streamable HTTP servers:

```yaml
enabled: true
transport: streamable_http
url: "https://example.com/mcp"
headers:
  Authorization: "Bearer ${MCP_EXAMPLE_TOKEN}"
```

Only `env` and `headers` support `${ENV_VAR}` interpolation. If a referenced
environment variable is missing, that server is skipped for the turn and the
host emits an MCP diagnostic event.

MCP tool names are exposed to the model as
`<safe_server_name>__<safe_tool_name>`. The host calls the original MCP tool
name on the original server. MCP tool calls require a capability, defaulting to
`mcp.call:<server_id>`, so the concrete core must declare it:

```yaml
capabilities:
  defaults:
    mcp.call:docs: {}
```

The first MCP implementation exposes server tools only. MCP resources, prompts,
OAuth, dynamic discovery, and CLI management commands are not part of the first
core-YAML surface.

## Structured Results With `ctx.result`

Output modules can use `ctx.result` to return code-level structured data to a
parent core that called them. `ctx.result` is not delivered to the user and is
not automatically written to parent history. The parent must explicitly read
`ctx.agents.run(...).result` and decide whether to call its own
`ctx.output.send_*`.

`ctx.result.set({...})` accepts JSON-compatible values. If multiple serial
output modules set dictionaries, they are shallow-merged and later modules
override duplicate keys. Setting a non-dictionary value replaces the current
result. Python `return` values from authored modules are still ignored by the
host and cannot be used as results.

Only serial output modules can write the current turn result. Parallel output
modules cannot change the current result, which avoids making parent-visible
structured data depend on async completion order.

A TTS-style child core can return a JSON-compatible audio descriptor:

```python
def process(ctx):
    ctx.result.set({
        "audio": {
            "path": "voice.ogg",
            "kind": "audio",
            "media_type": "audio/ogg",
            "summary": "short voice summary",
        },
        "transcript": "Short summary text",
    })
```

The parent core is then responsible for delivery:

```python
async def process(ctx):
    tts = await ctx.agents.run("tts", "summarize this turn as short speech")
    audio = tts.result["audio"]
    ctx.output.send_audio(
        audio["path"],
        media_type=audio.get("media_type"),
        summary=audio.get("summary"),
        artifact_metadata=audio.get("metadata"),
        history_policy="model_hidden",
    )
```

## Schedules

A minimal schedule:

```yaml
schedule: "0 9 * * *"
prompt: "Write a daily project summary."
```

This is equivalent to:

```yaml
enabled: true
timezone: "UTC"
modules:
  input: [base_input]
  output: [base_output]
delivery:
  mode: local
```

`modules.input` and `modules.output` must reference existing input/output
modules in the current core. A schedule module list is a schedule-local serial
list. It does not run the core's full pipeline and does not run parallel
modules.

`delivery.mode: local` writes session output and the schedule run log. It does
not proactively send output to TUI or Telegram.

Telegram proactive delivery must explicitly declare a target, and that target
must be listed in the same core under `channels.telegram.allowed_users` or
`channels.telegram.allowed_chats`:

```yaml
delivery:
  mode: telegram
  chat_id: 123456789
```

See [schedules.md](schedules.md) for details.

## Send History Policy

`history_policy` controls whether a delivery is written to `messages.jsonl` and
whether it enters later model context.

| Policy | Written to `messages.jsonl` | Enters later model context | Typical use |
| --- | --- | --- | --- |
| `persist` | Yes | Yes | Normal assistant replies and content the model should remember. |
| `model_hidden` | Yes | No | User-visible records, background results, and audit-visible content that should not pollute prompts. |
| `transient` | No | No | Progress, notices, debug status, and one-time display. |

When `ctx.input/ctx.output.send_text()`, `send_image()`, `send_audio()`,
`send_video()`, or `send_file()` omit `history_policy`, they use the current
slot's `history_policy`.

Use `delivery_metadata={...}` only for host-visible metadata attached to a
specific delivery. For media/file `send_*`, the first argument accepts only a
workspace/session path, URL, or host-returned `ArtifactRef`. Use `summary=...`
and `artifact_metadata={...}` for artifact summary or source information
instead of passing an internal artifact descriptor dictionary.

Media/file `send_*` registers the path as an artifact and emits the
corresponding media/file delivery. `progress()` and `notice()` always use
`transient`.

`delivery` controls channel timing. It does not change whether the item is
written to session history:

| Delivery | Behavior |
| --- | --- |
| `immediate` | Default. Commit history at the call site and queue the channel item immediately in route order. |
| `slot_end` | Commit history at the call site, then queue delivery after the current slot succeeds. |

Use `persist` for content that should enter model context:

```python
ctx.output.send_text("Final summary", history_policy="persist")
```

Use `model_hidden` for content that should be shown immediately but not enter
model context:

```python
ctx.output.send_text("Background task completed", history_policy="model_hidden", delivery="immediate")
```

Use `transient` for temporary display:

```python
ctx.output.progress("Working...")
ctx.output.send_text("Temporary note", history_policy="transient", delivery="immediate")
```

## Verification

Structural checks:

```bash
uv run demiurge doctor
uv run demiurge init --check
```

Local TUI check:

```bash
uv run demiurge --provider fake
```

Telegram check:

```bash
export DEMIURGE_TELEGRAM_BOT_TOKEN="..."
uv run demiurge gateway --core assistant
```

Inspect session history:

```bash
ls ~/.demiurge/sessions
tail -n 20 ~/.demiurge/sessions/<session_id>/messages.jsonl
tail -n 50 ~/.demiurge/sessions/<session_id>/events.jsonl
```

Messages with `model_visible: false` do not enter later model context.
`transient` deliveries are not written to `messages.jsonl`, but they still
appear in the event log.

## Further Reading

- [Agents](agents.md): agent core structure and runtime initialization.
- [Tools](tools.md): built-in tools, permissions, and approvals.
- [Skills](skills.md): `agent/skills/` and progressive loading.
- [Sessions and Context](sessions.md): session history, context assembly, and compaction.
