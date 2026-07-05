---
title: Write an Agent Slot
description: Add bootstrap, input, or output behavior to an Agent Core.
---

# Write an Agent Slot

Use an Agent Slot when a core needs authored behavior at a governed point in
the agent loop:

- `bootstrap` adds session-stable context once per session.
- `input` shapes the current turn before the provider call.
- `output` handles the final model output after the provider call.

This guide edits a concrete runtime core. With the default runtime layout, the
core is under:

```text
~/.demiurge/agents/<core_id>/
```

Do not edit `~/.demiurge/agents/agent.yaml` when adding a slot to one core.
That file is the global fallback config. Concrete cores contain their own
`agent.yaml` plus an authored surface such as `agent/`.

## Before You Start

Check that runtime cores load:

```bash
uv run demiurge init --check
```

Open the target core and confirm it has:

```text
agent.yaml
agent/pipelines.yaml
```

The core's `runtime.surface_root` is usually `agent`. Bootstrap, input, and
output slot roots are resolved from that surface root:

| Phase | Root |
| --- | --- |
| `bootstrap` | `agent/bootstrap/<slot_id>/` |
| `input` | `agent/input/<slot_id>/` |
| `output` | `agent/output/<slot_id>/` |

Changing `slots.input` or `slots.output` in `agent.yaml` does not move these
phase roots.

## Choose the Slot Phase

Choose the phase by the behavior you need:

| Need | Use |
| --- | --- |
| Add memory, identity, or session-stable context before turns | `bootstrap` |
| Add instructions, normalize raw input, inspect attachments, or activate skills before the model call | `input` |
| Deliver the model response, transform output, emit artifacts, write result data, or update state after the model call | `output` |
| Expose an explicit model-callable action | Authored tool, not a slot |
| Share helper code between slots/tools | `agent/lib/`, not a slot |

Prefer adding a named slot and updating `agent/pipelines.yaml`. Do not rewrite
`base_input` or `base_output` unless the change is intentionally replacing the
seed input or output behavior.

## Create the Slot Directory

For an input slot named `style_hint`, create:

```text
agent/input/style_hint/
  module.py
  slot.yaml
```

The directory name is the slot id used in `agent/pipelines.yaml`.

## Write `module.py`

The default entrypoint is `module:process`, relative to the slot directory.
The callable can be synchronous or asynchronous.

### Bootstrap Example

```python
def process(ctx):
    ctx.bootstrap.add("Session note: prefer concise, concrete answers.")
```

Bootstrap slots run once per session before turns begin. Bootstrap return
values are ignored; write session-stable context through `ctx.bootstrap.add(...)`.

### Input Example

```python
def process(ctx):
    ctx.input.add_context(
        "For this turn, prefer short answers with concrete next steps.",
        role="system",
    )
```

Input slots run before the provider call. The seed `base_input` slot appends
the raw user text:

```python
def process(ctx):
    ctx.input.add_context(ctx.input.raw_text, role="user")
```

If no input slot produces user text, the turn fails.

### Output Example

```python
def process(ctx):
    ctx.output.send_text(ctx.output.response_text)
```

Output slots run after the provider response. The seed `base_output` slot
delivers the model response. If you remove or skip `base_output`, another
output slot must deliver or record the response.

## Use Common `ctx` APIs

For the full parameter reference, see [Slot Context SDK](../reference/slot-context-sdk.md).

### Read Input Text and Attachments

```python
def process(ctx):
    if ctx.input.attachments:
        ctx.input.add_context(
            f"The user attached {len(ctx.input.attachments)} item(s).",
            role="system",
        )
```

### Send Text or Artifacts

```python
def process(ctx):
    ctx.output.send_text(
        "Archive complete.",
        history_policy="model_hidden",
        history_text="The archive step completed.",
    )
    ctx.output.send_file(
        "reports/summary.pdf",
        caption="Summary report",
        media_type="application/pdf",
        history_text="Sent the summary report PDF.",
    )
```

Artifact paths must be inside the workspace or the session artifact root.
Non-text deliveries that write history should provide `history_text`.

### Emit Status

```python
def process(ctx):
    ctx.output.progress("Preparing audio...")
    ctx.output.notice("Audio generation skipped because no voice is configured.")
```

`progress(...)` and `notice(...)` are transient status deliveries. They do not
write assistant history.

### Read Session History

```python
def process(ctx):
    recent = ctx.history.recent_messages(4, roles={"user", "assistant"})
    summary = "\n".join(f"{item.role}: {item.content}" for item in recent)
    ctx.input.add_context(f"Recent conversation:\n{summary}", role="system")
```

`ctx.history` exists on input and output slots. Bootstrap slots and authored
tools do not receive it.

### Write Core or Session State

```python
def process(ctx):
    count = ctx.state.session.get("summary_count", 0)
    ctx.state.session.set("summary_count", count + 1)
    ctx.state.core.merge("preferences", {"summary_style": "short"})
```

State reads and writes require capabilities such as `state.session.read`,
`state.session.write`, `state.core.read`, or `state.core.write`.

### Call a Tool

```python
async def process(ctx):
    result = await ctx.tools.call("tools_list")
    ctx.output.notice(result.content[:200])
```

The slot needs `tool.call:<tool_name>` capability, and the tool must be visible
to the current core.

### Run a Child Agent

```python
async def process(ctx):
    result = await ctx.agents.run(
        "assistant",
        "Summarize this response for the parent output slot.",
        input_slots=["base_input"],
        output_slots=["base_output"],
        tools="none",
    )
    ctx.result.set({"child_summary": result.content})
```

Use `ctx.agents.spawn(...)` instead of `run(...)` when the child should continue
as a background `agent.spawn` task.

### Activate a Skill

```python
def process(ctx):
    ctx.skills.activate("release-checklist")
```

Only input slots receive `ctx.skills`. The slot needs `skill.activate` or
`skill.activate:<skill>` capability.

## Declare `slot.yaml`

Create `slot.yaml` next to `module.py`:

```yaml
entrypoint: module:process
description: "Adds a current-turn style hint."
input_schema: {}
capabilities: []
timeout_seconds: null
failure_policy: soft
default_placement: pre_current_user
history_policy: persist
```

Accepted fields are exactly:

| Field | Default | Notes |
| --- | --- | --- |
| `entrypoint` | `module:process` | `module:function`, or a core-root-relative Python file path plus function. |
| `description` | `""` | Human-facing description for inspection. |
| `input_schema` | `{}` | Author metadata; the slot loader accepts it. |
| `capabilities` | `[]` | Capabilities this slot may require through `ctx.capability.require(...)` or through SDK clients. |
| `timeout_seconds` | `null` | Loaded as metadata; the current slot invoker does not enforce a timeout. |
| `failure_policy` | `soft` | `soft` logs and continues; `hard` fails the turn or bootstrap. |
| `default_placement` | `pre_current_user` | Default placement metadata for legacy context contribution shapes. |
| `history_policy` | `persist` | Default delivery history policy for output/tool-style sends. |

Unknown fields are rejected.

Declare capabilities before using guarded APIs:

```yaml
capabilities:
  - state.session.read
  - state.session.write
  - tool.call:tools_list
  - agents.run:assistant
```

Capability grants do not bypass host approval, workspace scope, command guards,
channel policy, or tool runtime rules.

## Add the Slot to the Existing Pipeline

Open the existing `agent/pipelines.yaml`. Insert the new slot id into the
appropriate existing list.

For an input slot that should run before raw user text is appended:

```yaml
input:
  serial:
    - style_hint
    - base_input
```

For an output slot that should run after the seed output delivery:

```yaml
output:
  serial:
    - base_output
    - archive_summary
```

For a bootstrap slot:

```yaml
bootstrap:
  serial:
    - session_context
```

Do not replace the whole file. Preserve the current `schema_version`,
`bootstrap`, other phase entries, and any existing `parallel` lists unless the
change intentionally modifies them.

## Choose Serial or Parallel

Use `serial` when the slot must affect the main flow. Serial input slots can
modify the prompt. Serial output slots can write history and set
`ctx.result`.

Use `parallel` only for background side effects:

- Parallel input slots cannot modify the current prompt.
- Parallel output slots cannot write session history.
- Parallel output slots cannot modify `ctx.result`.

Bootstrap supports only `serial`.

## Verify

Run:

```bash
uv run demiurge init --check
uv run demiurge --provider fake
```

If the core fails to load, compare the slot directory with
[the Agent Slot contract](../reference/contracts/slot-modules.md). For
evolution worktrees, keep edits inside the authored surface and follow
[the evolver-safe edit contract](../reference/contracts/evolver-safe-edits.md).
