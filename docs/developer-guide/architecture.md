---
title: Architecture
description: Map the current Demiurge host runtime for contributors.
---

# Architecture

This guide maps the current implementation. It is not a stable plugin API.

## System Overview

```text
CLI / TUI / Gateway
        |
        v
create_app()
        |
        v
SessionTurnStepRunner
        |
        +--> ContextAssembler
        +--> Provider
        +--> ToolRuntime
        +--> Delivery/session stores
        +--> SchedulerService
```

## Major Subsystems

| Subsystem | Responsibility |
| --- | --- |
| CLI | Parse commands and start TUI, gateway, setup, package, update, and doctor flows. |
| App factory | Resolve runtime home, config, source templates, core, workspace, provider, approvals, and tools. |
| Core loader | Load `agent.yaml`, slots, pipelines, skills, schedules, and MCP declarations. |
| Runner | Own session, turn, step, bootstrap, input, model/tool loop, output, and delivery flow. |
| Context assembler | Build provider messages from soul, skills, bootstrap, input, history, and current turn. |
| Tool runtime | Build the visible registry and execute built-in, authored, and MCP tools. |
| Delivery runtime | Convert authored delivery requests into session records, events, artifacts, and channel output. |
| Scheduler | Claim due schedules and run fresh sessions. |
| Package manager | Preview, install, uninstall, and record package repository components. |

## Entry Points

- `demiurge/cli.py`
- `demiurge/app/__init__.py`
- `demiurge/runtime/runner.py`
- `demiurge/tools/runtime.py`
- `demiurge/channels/gateway.py`
- `demiurge/packages.py`
- `demiurge/scheduler/__init__.py`

## Reading Order

1. [runner-and-context.md](runner-and-context.md)
2. [tool-runtime.md](tool-runtime.md)
3. [delivery-runtime.md](delivery-runtime.md)
4. [package-installer.md](package-installer.md)
5. [scheduler.md](scheduler.md)
6. [mcp-runtime.md](mcp-runtime.md)

## Boundary

When a runtime change affects user-visible behavior, CLI/configuration, package
recipes, runtime layout, security policy, provider behavior, state/versioning,
or test/gate workflow, update the public manual in the same change.
