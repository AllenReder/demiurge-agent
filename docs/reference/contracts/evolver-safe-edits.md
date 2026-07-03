---
title: Evolver-Safe Edit Contract
description: Stable rules for the host-managed evolver core.
---

# Evolver-Safe Edit Contract

The `evolver` core edits an isolated agents-tree worktree after the active core
requests evolution. The host creates the worktree and performs gating and
promotion.

This contract defines safe edit scope for candidate work.

## Candidate Scope

The editable target is an isolated candidate agents tree worktree, not the
source checkout and not host runtime state. The evolver usually edits one
target concrete core, but it may also edit helper cores when the goal requires
cross-core behavior.

Safe candidate shape:

```text
agents/
  agent.yaml
  <core>/
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

## Preferred Edit Paths

Prefer edits under:

```text
agent/skills/
agent/tools/
agent/input/
agent/output/
agent/bootstrap/
agent/pipelines.yaml
```

Allowed with care:

```text
agent/SOUL.md
agent/schedules/
agent/mcp/
agent/lib/
agent.yaml
```

Change `agent.yaml` only when it is the minimum needed to keep the candidate
loadable or to declare a required authored-surface capability, tool root, MCP
root, schedule root, channel config, or metadata override.

## Forbidden Paths

Do not edit:

- source checkout files
- host config
- `agents/agent.yaml` global fallback config
- session records
- runtime SQLite files
- scheduler/runtime task state
- production state
- release files
- dependency files
- runtime files outside the candidate workspace
- `.temp/` reference checkouts
- package repository source files unless the explicit goal is package authoring
  and the candidate workspace contains them

## Forbidden Actions

Do not:

- promote a candidate manually
- roll back the live Git ref manually
- install dependencies
- change the host lock file
- run broad destructive cleanup
- edit files outside the candidate workspace
- bypass host file, terminal, network, tool, channel, or state capabilities

## Pipeline Edit Rule

When adding a slot, edit the existing list in `agent/pipelines.yaml`.

Good:

```yaml
input:
  serial:
    - concise_hint
    - base_input
```

Bad:

```text
Replace pipelines.yaml with a minimal file that omits unrelated phases.
```

Keep unrelated phase entries and existing seed slots unless the goal explicitly
requires changing them.

## Good Evolution Goals

Good goals are functional and scoped:

```text
Add an input module that gives Telegram replies a concise style hint.
Change only agent/input and agent/pipelines.yaml.
```

Bad goals ask for host runtime changes, dependency changes, release changes, or
unbounded rewrites.

## Finish Criteria

At the end of an evolution run, summarize:

- changed behavior
- candidate files edited
- verification performed
- limitations or follow-up needed

The host performs gates and promotion through `CoreRepository`.
