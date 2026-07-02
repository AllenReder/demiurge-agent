---
title: Authored Surface Contract
description: Stable rules for files owned by an Agent Core.
---

# Authored Surface Contract

This page defines the authored surface of a Demiurge Agent Core. It is intended
for human authors and for the `evolver` core when project docs are supplied as
read-only reference context.

## Core Root

A concrete runtime core has this shape:

```text
<core>/
  agent.yaml
  packages.yaml
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
    tests/
```

`packages.yaml` is package install state. Do not edit it manually unless you are
repairing package state with explicit user direction.

## Owned by the Agent Core

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
- `agent/tests/`

## Owned by the Host

Agent Core authors must not take ownership of:

- provider request construction
- provider calls
- session, turn, and step storage
- tool registry and dispatch
- approval decisions
- workspace enforcement
- production state mutation
- package repository trust
- dependency installation
- promotion or rollback

## Dependency Rule

Current runtime mode is `host_shared`. Agent Slot code runs in the host
Python environment. Candidate cores must not add Python dependencies
automatically. If a change needs a dependency, document it as a manual dependency
review item.

## Slot Rule

Bootstrap, input, and output slots are directory components. Slot code and
metadata stay together under `agent/<bootstrap|input|output>/<slot_id>/`; each
slot declares metadata in `slot.yaml`. Phase ordering lives in
`agent/pipelines.yaml`.

`base_input` and `base_output` are editable seed slots. The host does not treat
them as required built-ins. If no input slot contributes prompt content, the
turn fails; if no output slot sends or records the assistant response, the raw
provider response remains only in task/debug records.

## Verification

After authored-surface edits:

```bash
uv run demiurge init --check
uv run demiurge --provider fake
```

Use narrower checks from the relevant page when editing packages, schedules, MCP
servers, or tools.
