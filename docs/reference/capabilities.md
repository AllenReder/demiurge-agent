---
title: Capabilities and Approvals
description: Reference for host-owned capability and approval behavior.
---

# Capabilities and Approvals

Capabilities are host-owned grants for effect classes. Approval policy decides
whether a requested effect can run automatically, must prompt, or is denied.

Declaring a tool, slot, schedule, or MCP server is not enough to run dangerous
effects. The host checks capabilities at execution time.

## Capability Grants

Capabilities can be granted globally for the core:

```yaml
capabilities:
  defaults:
    fs.read:
      scope: workspace
    terminal.exec:
      scope: workspace
```

They can also be granted to one authored component path:

```yaml
capabilities:
  slots:
    agent/output/archive_summary:
      fs.write:
        scope: workspace
```

Slot and authored-tool manifests can also declare a local `capabilities` list:

```yaml
capabilities:
  - fs.read
  - tool.call:project_note
```

At runtime, authored code calls:

```python
ctx.capability.require("fs.read", slot_path=ctx.slot_path)
```

If the capability is not declared, the host raises `capability denied`.

## Prefix Grants

The capability checker supports exact keys and prefix wildcards:

```yaml
capabilities:
  defaults:
    mcp.call:*:
      scope: core
```

This grants capabilities such as `mcp.call:docs`.

## Common Capabilities

| Capability | Meaning |
| --- | --- |
| `fs.read` | Read host-visible files through host checks or an authored component that requires it. Outside-workspace and sensitive reads require approval. |
| `fs.write` | Write workspace files. |
| `terminal.exec` | Run terminal commands in workspace scope. |
| `network.fetch` | Fetch network content. |
| `schedule.manage` | Manage core schedule YAML files. |
| `task.control` | List, inspect, wait for, or cancel background runtime tasks. |
| `tool.call:<tool>` | Let authored code call a visible tool through `ctx.tools.call(...)`. |
| `mcp.call:<server>` | Let the model call tools from an MCP server. |
| `skill.activate` | Let input slots activate skills. |
| `skill.activate:<skill>` | Let input slots activate a specific skill. |
| `state.read` | Read host state through `ctx.state`. |
| `state.write` | Write host state through `ctx.state`. |
| `state.propose` | Submit legacy state proposal effects. |
| `agents.run:<core>` | Run a child agent synchronously. |
| `agents.spawn:<core>` | Spawn a child agent task. |
| `tool.call:evolve_core` | Start, review, promote, or discard a host-owned evolve run. |
| `tool.call:rollback_core` | Create a rollback commit for the live Agent Core tree. |

## Approval Policy

Approval policy values are:

```text
auto < prompt < deny
```

Risk values are:

```text
low < medium < high < critical
```

For most tools, the host starts from tool metadata, then applies core approval
overrides, then global fallback approval. More restrictive core policy wins over
tool metadata. Global fallback approval is host-level policy and can be used as
the final default.

## Core Approval Config

```yaml
approval:
  default: null
  tools:
    terminal: prompt
  capabilities:
    fs.write: prompt
  risks:
    critical: deny
```

`tools` matches tool names. `capabilities` matches the capability used for the
request. `risks` matches the request risk.

## Tool Registry Metadata

`tools.metadata` changes registry metadata:

```yaml
tools:
  metadata:
    web_extract:
      approval_policy: deny
      risk: medium
      capability: network.fetch
```

Built-in tools cannot be made less restrictive by core metadata. Authored and
MCP tools can be fully overridden because they are core-declared surfaces.

## Boundary

Capabilities are not a sandbox by themselves. The host still enforces workspace
scope, sensitive path checks, command guards, approval prompts, channel policy,
and tool runtime rules before effects execute.
