# Architecture

This page maps the current Demiurge implementation for contributors.

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

| Subsystem | Primary responsibility |
| --- | --- |
| CLI | Parse commands, create app, start TUI/gateway/package/update/doctor flows. |
| App factory | Resolve runtime home, source templates, core, workspace, provider, tool runtime, approvals. |
| Core loader | Load `agent.yaml`, slot roots, pipelines, skills, schedules, MCP declarations. |
| Runner | Own session/turn/step flow, bootstrap, input phase, model loop, output phase, delivery records. |
| Context assembler | Build provider messages from soul, skill index, bootstrap, input placements, history, current turn. |
| Tool runtime | Build visible registry and execute built-in, authored, and MCP tools. |
| Delivery runtime | Convert authored delivery requests into artifacts, session messages, events, and channel items. |
| Scheduler | Claim due core-authored schedules and run fresh sessions. |
| Package manager | Preview, install, and uninstall package repository components into runtime cores. |

## Entry Points

- `demiurge/cli.py`: `demiurge`, `init`, `doctor`, `package`, `update`, `gateway`.
- `demiurge/app/__init__.py`: `create_app`, runtime initialization, provider/config resolution.
- `demiurge/ui/tui_launcher.py` and `demiurge/ui_gateway/`: TUI process bridge.
- `demiurge/channels/gateway.py`: external channel startup.

## Data Flow

```text
User/channel input
  -> InteractionInbound
  -> SessionTurnStepRunner.run_turn()
  -> bootstrap if needed
  -> input pipeline
  -> ContextAssembler
  -> provider request
  -> tool calls through ToolRuntime until final response
  -> output pipeline
  -> InteractionOutbound / session records / events
```

## Recommended Reading Order

1. [runner-and-context.md](runner-and-context.md)
2. [tool-runtime.md](tool-runtime.md)
3. [delivery-runtime.md](delivery-runtime.md)
4. [scheduler.md](scheduler.md)
5. [mcp-runtime.md](mcp-runtime.md)
6. [package-installer.md](package-installer.md)

## Boundary

This guide documents current implementation shape. It does not define a stable
plugin API or promise compatibility with old runtime layouts during the current
alpha phase.
