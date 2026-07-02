---
title: Evolver-Safe Edit Contract
description: Stable rules for the host-managed evolver core.
---

# Evolver-Safe Edit Contract

The `evolver` core edits a candidate copy of another Agent Core after the active
core requests evolution. This page defines the safe edit scope.

## Allowed Candidate Paths

Prefer edits under:

```text
agent/skills/
agent/tools/
agent/input/
agent/output/
agent/bootstrap/
```

Allowed with care:

```text
agent/SOUL.md
agent/schedules/
agent/mcp/
agent/lib/
agent/tests/
```

Change `agent.yaml` only when it is the minimum needed to keep the candidate
loadable after an authored-surface edit.

## Forbidden Paths

Do not edit:

- source checkout files
- host config
- registry files
- session records
- scheduler state
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
- roll back the active pointer manually
- install dependencies
- change the host lock file
- run broad destructive cleanup
- edit files outside the candidate workspace
- bypass host file, terminal, network, or state capabilities

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
- any limitations or follow-up needed

The host performs manifest checks and promotion.
