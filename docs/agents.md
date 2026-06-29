# Agents

## Source Templates

Source agent cores live at the repository root:

```text
agents/
  agent.yaml
  assistant/
    agent.yaml
    agent/
  evolver/
    agent.yaml
    agent/
```

`agents/agent.yaml` is the global fallback config template, not an agent core.
It may contain `model`, `ui`, and `approval`. It must not contain concrete
agent-bound fields such as `agent`, `slots`, `tools`, `channels`, or
`capabilities`.

## Runtime Cores

Runtime agent cores live under:

```text
~/.demiurge/agents/<core_id>/
```

Normal startup fills in missing runtime templates without overwriting user
edits. Explicit `demiurge init` backs up and refreshes templates.

See [agent-core-authoring.md](agent-core-authoring.md) for a practical guide to
customizing the runtime assistant.

## Agent Core Structure

An agent core is `agent.yaml + agent/`:

```text
assistant/
  agent.yaml
  agent/
    SOUL.md
    input/
      pipeline.yaml
      <module>/
        slot.yaml
        module.py
    output/
      pipeline.yaml
      <module>/
        slot.yaml
        module.py
    tools/
    skills/
    schedules/
```

The host owns the loop, context assembly, provider calls, tool execution, and
interaction bridges. Agent cores contribute authored surface and may declare
external channel config in `agent.yaml`. Gateway mode starts external channel
adapters enabled by the current core. Channels are host adapters, not
`agent/` slots.

## Input and Output Pipelines

Every core must include:

- `agent/input/pipeline.yaml`
- `agent/output/pipeline.yaml`

Pipeline files declare `serial` and `parallel` module groups. `serial` modules
run in order and are awaited. `parallel` modules run from a phase-entry
snapshot and cannot change the current prompt, current output result, or
current `ctx.result`.

Input modules run before model calls. The host does not automatically add
channel input to the prompt. Modules must explicitly call
`ctx.input.add("user", ctx.input.raw_input.text)` or add `system` fragments.

Output modules run after the final model output. The host does not
automatically deliver assistant text. Modules must explicitly call
`ctx.output.send_text(ctx.output.content)` or another `send_*` method.

`slot.yaml` describes one module: entrypoint, failure policy, history policy,
capabilities, and local metadata. Global order and parallelism belong to the
pipeline file.

## Boundary Summary

| Item | Rule |
| --- | --- |
| pipeline `serial` | awaited in declaration order |
| pipeline `parallel` | background work from a phase-entry snapshot |
| input `system` | enters only the current model request |
| input `user` | joined into the current user message in serial order |
| `send_*` default | commits history at the call site and uses immediate delivery |
| `ctx.result.set(dict)` | shallow merges in serial output order |
| `progress` / `notice` | transient immediate delivery |
| `delivery="slot_end"` | records at call site, queues after slot success |
| IO send capability | built into input/output slots; high-risk host APIs still need capabilities |

The runtime `evolver` core is copied like other templates so users can adjust
model config. Automated evolution of another core must not write into the
runtime evolver directory.
