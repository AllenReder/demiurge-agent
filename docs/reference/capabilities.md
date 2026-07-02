---
title: Capabilities and Approvals
description: Reference for host-owned capability and approval behavior.
---

# Capabilities and Approvals

Capabilities describe effect classes. Approval policy decides whether a call can
run automatically, must prompt, or is denied.

## Common Capabilities

| Capability | Meaning |
| --- | --- |
| `fs.read` | Read workspace files. |
| `fs.write` | Write workspace files. |
| `terminal.exec` | Run terminal commands in workspace scope. |
| `job.control` | List, poll, wait, read logs for, or cancel background runtime tasks. |
| `agents.spawn:<core_id>` | Spawn a child agent task for a specific core. |
| `network.fetch` | Fetch network content. |
| `schedule.manage` | Manage core schedule files. |
| `tool.call:evolve_core` | Create and promote a candidate core through the host. |
| `tool.call:rollback_core` | Roll back active core pointer through the host. |

## Approval Policy

Approval policy order:

```text
auto < prompt < deny
```

Risk order:

```text
low < medium < high < critical
```

More restrictive policy wins when multiple layers apply.

## Tool Metadata

`agent.yaml` can override metadata:

```yaml
tools:
  metadata:
    web_extract:
      approval_policy: prompt
      risk: medium
      capability: network.fetch
```

Supported metadata keys:

- `risk`
- `capability`
- `approval_policy`
- `model_output_policy`
- `display_policy`
- `enabled`

## Boundary

Declaring a capability is not the same as receiving it. The host checks
workspace scope, sensitive paths, approval policy, and tool runtime rules before
executing effects.
