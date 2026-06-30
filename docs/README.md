---
slug: /
sidebar_position: 0
title: demiurge Manual
description: Production-oriented documentation for demiurge, a Python agent framework for self-evolving agents with modular capabilities and capability packages.
---

# demiurge Manual

demiurge is a Python agent framework for building self-evolving agents.
Independent Agent Cores carry identity and boundaries, while modular design and
capability package management make tools, IO, skills, and child cores
installable, composable, and iterative.

The host owns the runtime loop, provider calls, tools, approvals, state,
delivery, promotion, and rollback. Capability evolution stays inside this clear
runtime boundary.

This manual is organized as a production tool guide. Start with a running
agent, then learn the host/core boundary, then author modules and operate the
runtime safely.

## Start Here

| Goal | Read |
| --- | --- |
| Install and run the fake provider | [getting-started/quickstart.md](getting-started/quickstart.md) |
| Configure a real model provider | [getting-started/configure-provider.md](getting-started/configure-provider.md) |
| Understand what the host owns | [concepts/host-and-agent-core.md](concepts/host-and-agent-core.md) |
| Customize an agent core | [authoring/agent-core-layout.md](authoring/agent-core-layout.md) |
| Diagnose runtime drift | [getting-started/update-and-doctor.md](getting-started/update-and-doctor.md) |

## Learning Path

1. Run `scripts/install.sh` or `uv run demiurge --provider fake`.
2. Read the host/core boundary before editing runtime cores.
3. Add one input module and one output module.
4. Install a package into a runtime core and inspect the changes.
5. Configure approvals, workspace scope, and Telegram only after local TUI works.

## Concepts

| Page | Purpose |
| --- | --- |
| [concepts/host-and-agent-core.md](concepts/host-and-agent-core.md) | Runtime boundary between host-owned harness and authored agent core. |
| [concepts/runtime-home.md](concepts/runtime-home.md) | Runtime directory layout under `~/.demiurge`. |
| [concepts/sessions-and-context.md](concepts/sessions-and-context.md) | Durable sessions, context assembly, resume, and compaction. |
| [concepts/security-model.md](concepts/security-model.md) | Workspace scope, sensitive paths, approvals, and channel trust boundaries. |

## Authoring Agent Cores

| Page | Purpose |
| --- | --- |
| [authoring/agent-core-layout.md](authoring/agent-core-layout.md) | `agent.yaml + agent/` layout and slot roots. |
| [authoring/bootstrap-modules.md](authoring/bootstrap-modules.md) | Session-start context modules. |
| [authoring/input-modules.md](authoring/input-modules.md) | Input shaping before model requests. |
| [authoring/output-modules.md](authoring/output-modules.md) | Output delivery after model responses. |
| [authoring/authored-tools.md](authoring/authored-tools.md) | Core-local tools executed by the host. |
| [authoring/skills.md](authoring/skills.md) | Progressive skill loading from `agent/skills/`. |
| [authoring/mcp.md](authoring/mcp.md) | Core-local MCP server declarations. |
| [authoring/schedules.md](authoring/schedules.md) | Core-declared cron schedules executed by the host scheduler. |
| [authoring/packages.md](authoring/packages.md) | Install reusable catalog components into runtime cores. |
| [authoring/testing-agent-cores.md](authoring/testing-agent-cores.md) | Structural and runtime checks for authored cores. |

## Operations

| Page | Purpose |
| --- | --- |
| [operations/configuration.md](operations/configuration.md) | Host config, fallback agent config, workspace, approvals, and channels. |
| [operations/channels.md](operations/channels.md) | TUI and external channel behavior. |
| [operations/telegram.md](operations/telegram.md) | Telegram setup, allowlists, delivery, and approvals. |
| [operations/package-management.md](operations/package-management.md) | Package wizard and scripted install/uninstall workflow. |
| [operations/troubleshooting.md](operations/troubleshooting.md) | Common failures and recovery steps. |

## Reference

| Page | Purpose |
| --- | --- |
| [reference/cli.md](reference/cli.md) | Command-line flags and subcommands. |
| [reference/agent-yaml.md](reference/agent-yaml.md) | Concrete core and global fallback YAML fields. |
| [reference/slot-yaml.md](reference/slot-yaml.md) | Module and authored-tool slot metadata. |
| [reference/tools.md](reference/tools.md) | Built-in toolsets, authored tools, MCP tools, and output shaping. |
| [reference/capabilities.md](reference/capabilities.md) | Capability and approval boundaries. |
| [reference/history-policy-and-delivery.md](reference/history-policy-and-delivery.md) | `history_policy`, delivery timing, and artifact delivery. |
| [reference/package-recipes.md](reference/package-recipes.md) | Catalog package recipe fields. |
| [reference/runtime-layout.md](reference/runtime-layout.md) | Runtime files and source template mapping. |

## Developer Guide

| Page | Purpose |
| --- | --- |
| [developer-guide/architecture.md](developer-guide/architecture.md) | Entry points, major subsystems, and data flow. |
| [developer-guide/runner-and-context.md](developer-guide/runner-and-context.md) | Turn runner, phases, model loop, and context assembly. |
| [developer-guide/tool-runtime.md](developer-guide/tool-runtime.md) | Tool registry, dispatch, approvals, and workspace checks. |
| [developer-guide/delivery-runtime.md](developer-guide/delivery-runtime.md) | How authored deliveries become session records and channel output. |
| [developer-guide/scheduler.md](developer-guide/scheduler.md) | Host-owned schedule claims and run logs. |
| [developer-guide/mcp-runtime.md](developer-guide/mcp-runtime.md) | MCP catalog discovery, naming, env interpolation, and calls. |
| [developer-guide/package-installer.md](developer-guide/package-installer.md) | Package preview, install, uninstall, and registry state. |

## Short Path

```bash
uv run demiurge init
uv run demiurge --provider fake
```

For a real model, use an OpenAI-compatible endpoint and keep secrets in
environment variables:

```bash
export DEMIURGE_MODEL_NAME="gpt-4.1-mini"
export DEMIURGE_API_KEY="..."
uv run demiurge --provider openai
```

The default local entry is the TUI. External channels are started with:

```bash
uv run demiurge gateway --core assistant
```
