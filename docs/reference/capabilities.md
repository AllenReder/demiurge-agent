---
title: Capabilities and Approvals
description: Reference for host-owned capability and approval behavior.
---

# Capabilities and Approvals

Capabilities are host-owned grants for effect classes. Approval policy decides
whether a requested effect can run automatically, must prompt, or is denied.

Declaring a tool, slot, schedule, or MCP server does not grant a capability by
itself. Builtin and MCP call handlers generally check their required
capabilities at execution time. Authored tool dispatch requires the singular
registry capability before module import, while authored SDK clients continue
to enforce explicit `ctx.capability.require(...)` calls inside the module.

Current alpha limitation: MCP spawn/connect/discovery occurs before the later
call capability and approval check. Core mutation builtins now require the
resolved registry capability and effective approval policy before every
evolution/version-store adapter call or background task creation. See
[Host Runtime Contracts](../developer-guide/runtime-contracts.md#effectruntime)
for the single target ordering. Capabilities are not principal authorization or
a Python sandbox.

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
This plural implementation grant does not satisfy an authored tool's singular
registry `capability`; the pre-import dispatcher gate requires a core default
or path-scoped capability grant.

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
| `state.core.read` | Read core-scoped host state through `ctx.state.core`. |
| `state.core.write` | Write core-scoped host state through `ctx.state.core`. |
| `state.session.read` | Read session-scoped host state through `ctx.state.session`. |
| `state.session.write` | Write session-scoped host state through `ctx.state.session`. |
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

For authored tools and core mutation builtins, the Host starts from tool
metadata and applies applicable core/global approval policy monotonically:
core/global `auto` cannot weaken registry `prompt` or `deny`. Other builtin
handlers and MCP calls retain their documented handler-specific resolution.
Global fallback approval cannot lower a terminal command guard result from
`prompt/high` to automatic execution. Only `allow/low` terminal commands can be
auto-approved; hardline blocks terminate before approval.

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
MCP registry entries can be overridden because they are core-declared surfaces,
but the authored enforcement limitation above still applies.

## Boundary

Capabilities are not a sandbox by themselves. The Host's supported builtin and
SDK paths also apply workspace, sensitive-path, command, approval, channel, and
tool rules. In the default `host_shared` mode, imported authored Python can use
ordinary Python/OS APIs outside those SDK paths. The target `EffectRuntime`
centralizes model-triggered effect policy without claiming process isolation.
