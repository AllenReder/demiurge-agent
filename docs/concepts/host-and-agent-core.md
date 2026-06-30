# Host and Agent Core

demiurge separates the stable runtime harness from the authored agent surface.
This boundary is the main design rule for using and extending the framework.

## Mental Model

```text
Host harness
  owns sessions, turns, steps, context assembly, provider calls,
  tool scheduling, approvals, workspace checks, state, delivery,
  version registry, promotion, rollback, channels, scheduler

Agent core
  owns agent.yaml + agent/
  declares soul, bootstrap modules, input modules, output modules,
  authored tools, skills, schedules, MCP servers, lib code, tests
```

The host loads declarations and calls controlled extension points. Agent-core
code should not bypass the host by writing transcript files, calling provider
APIs directly, or talking to channel SDKs directly.

## What the Host Owns

| Area | Host responsibility |
| --- | --- |
| Runtime loop | Sessions, turns, model steps, tool-call loop, resume, compaction. |
| Provider calls | Request construction, provider adapter, tool-call/result ordering. |
| Tools | Registry, scheduling, workspace scope, approval, result shaping. |
| Delivery | History policy, artifacts, route context, TUI/Telegram dispatch. |
| State and versions | Typed state proposals, active core pointers, promotion, rollback. |
| Channels | TUI and Telegram bridges, authorization, approvals, busy behavior. |
| Scheduler | Durable schedule state, run claims, run logs, delivery targets. |

## What an Agent Core Owns

| Authored surface | Purpose |
| --- | --- |
| `agent.yaml` | Core identity, slot roots, tools, capabilities, model defaults, channels, runtime limits. |
| `agent/SOUL.md` | Core identity and stable behavior instructions. |
| `agent/bootstrap/` | Session-start context generated once and snapshotted. |
| `agent/input/` | Per-turn prompt shaping before the model request. |
| `agent/output/` | Post-model delivery and structured result production. |
| `agent/tools/` | Core-local tools exposed through the host tool runtime. |
| `agent/skills/` | Progressive knowledge documents loaded by `skill_view`. |
| `agent/schedules/` | Cron declarations executed by the host scheduler. |
| `agent/mcp/` | MCP server declarations; host owns transport and calls. |
| `agent/lib/` | Shared authored helpers used by slots and tools. |
| `agent/tests/` | Core-local test assets and gate inputs. |

## Why This Boundary Exists

The core can evolve quickly because it is file-backed, diffable, testable, and
replaceable. The host stays stable enough to enforce approvals, workspace
scope, provider request shape, session history, and rollback.

Candidate core changes must not add Python dependencies automatically. The
default runtime mode is `host_shared`: authored code runs in the host's
uv-managed Python environment. Per-core environments and subprocess workers are
future optional isolation modes, not the default.

## Success Check

Before adding a feature, decide which side owns it:

- If it changes provider calls, tool execution, session state, approvals, or
  channel adapters, it belongs to the host.
- If it changes how a specific core shapes input, formats output, exposes an
  authored tool, loads a skill, or declares a schedule/MCP server, it belongs
  to the agent core.

## Non-Goals

Agent cores are trusted authored Python code in the host-shared environment.
They are not containers, subprocess sandboxes, or independent package
environments in the current default runtime.
