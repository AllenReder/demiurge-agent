---
title: Authored Surface Contract
description: Stable rules for files owned by an Agent Core.
---

# Authored Surface Contract

This contract defines the authored surface of a Demiurge Agent Core. It is for
human authors and for the `evolver` core when docs are supplied as read-only
reference context.

## Core Identity

The global fallback config is not an Agent Core:

```text
agents/agent.yaml
```

A concrete Agent Core has its own manifest and authored surface:

```text
agents/<core>/
  agent.yaml
  agent/
    SOUL.md
    pipelines.yaml
    bootstrap/
    input/
    output/
    tools/
    skills/
    schedules/
    mcp/
    lib/
```

The same shape exists in the runtime home under
`~/.demiurge/agents/<core>/`.

## Loader Contract

For a concrete core, the loader requires:

- `<core>/agent.yaml`
- the directory named by `runtime.surface_root`
- `<surface_root>/pipelines.yaml`

With the default `runtime.surface_root: agent`, bootstrap, input, and output
slot roots are:

```text
agent/bootstrap/
agent/input/
agent/output/
```

These phase roots are not moved by `slots.input` or `slots.output`.

Skills, schedules, and MCP roots are inferred as `agent/skills`,
`agent/schedules`, and `agent/mcp` unless `slots.skills`, `slots.schedules`, or
`slots.mcp` are configured. Authored tools are discovered from the configured
`slots.tools` root.

## Core-Owned Files

Agent Core authors may edit:

- `agent.yaml`
- `agent/SOUL.md`
- `agent/pipelines.yaml`
- `agent/bootstrap/`
- `agent/input/`
- `agent/output/`
- `agent/tools/`
- `agent/skills/`
- `agent/schedules/`
- `agent/mcp/`
- `agent/lib/`

`packages.yaml` is package provenance state. It records installed package
targets and hashes, but it is not runtime truth. Edit it only during explicit
package state repair.

## Host-Owned Systems

Agent Core authors must not take ownership of:

- provider request construction
- provider profile resolution, provider-native request construction, and
  provider wire protocol conversion
- provider calls
- session, turn, step, message, artifact, and runtime event storage
- tool registry and dispatch
- approval decisions
- workspace enforcement
- production state mutation
- package repository trust
- dependency installation
- Git revision promotion or rollback
- gateway channel transport
- scheduler claims and run logs

## Slot Contract

Bootstrap, input, and output slots keep code and metadata together:

```text
agent/input/<slot_id>/
  module.py
  slot.yaml
```

The phase order lives only in `agent/pipelines.yaml`.

`base_input`, `base_output`, and `session_context` are editable seed slots in
the default core. They are not hidden host built-ins.

## Tool Contract

Authored tools are public Agent Core files:

```text
agent/tools/<tool_id>/
  tool.yaml
  module.py
```

They are model-callable actions, not pipeline slots. A tool's singular
`capability` is registry and approval metadata. Its `capabilities` list is what
lets the implementation satisfy `ctx.capability.require(...)`.

## Dependency Rule

Current runtime mode is `host_shared`. Authored Python code runs in the host's
uv-managed environment.

Candidate cores must not add Python dependencies automatically. If a change
needs a dependency, record it as a manual dependency review item.

## Verification

After authored-surface edits, run:

```bash
uv run demiurge init --check
uv run demiurge --provider fake
```

Use narrower checks from the relevant how-to or reference page when editing
tools, schedules, MCP servers, or channels.
