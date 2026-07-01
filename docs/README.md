---
slug: /
sidebar_position: 0
title: Demiurge Manual
description: English-first manual for running Demiurge, authoring Agent Cores, and building package repositories.
---

# Demiurge Manual

Demiurge is a local-first Python framework for file-backed, self-evolving Agent
Cores. The host owns the runtime harness. Agent Cores own the authored surface.
Package repositories install reusable capabilities into runtime cores.

This manual is organized with the Diataxis documentation model:

- **Tutorials** teach a complete path from zero to a working result.
- **How-to guides** solve specific tasks.
- **Explanation** pages describe why the system is shaped this way.
- **Reference** pages define exact commands, schemas, and contracts.

The reference contract pages are also intended to be readable by the `evolver`
core when it receives project docs as read-only context.

## Start Here

| Goal | Page |
| --- | --- |
| Start Demiurge locally | [tutorials/quick-start.md](tutorials/quick-start.md) |
| Make a safe Agent Core change | [tutorials/customize-agent-core.md](tutorials/customize-agent-core.md) |
| Create an external package repository | [tutorials/external-package-repository.md](tutorials/external-package-repository.md) |
| Configure a real model provider | [how-to/configure-provider.md](how-to/configure-provider.md) |
| Understand the host/core boundary | [explanation/host-and-agent-core.md](explanation/host-and-agent-core.md) |
| Read the stable authored-surface rules | [reference/contracts/authored-surface.md](reference/contracts/authored-surface.md) |

## Reading Paths

Alpha users should read:

1. [Quick Start](tutorials/quick-start.md)
2. [Configure a provider](how-to/configure-provider.md)
3. [Choose a workspace](how-to/choose-workspace.md)
4. [Troubleshoot](how-to/troubleshoot.md)

Agent Core authors should read:

1. [Host and Agent Core](explanation/host-and-agent-core.md)
2. [Customize an Agent Core](tutorials/customize-agent-core.md)
3. [Write a slot module](how-to/write-slot-module.md)
4. [Authored surface contract](reference/contracts/authored-surface.md)

Package and repository authors should read:

1. [Package model](explanation/package-model.md)
2. [Create an external package repository](tutorials/external-package-repository.md)
3. [Install packages](how-to/install-packages.md)
4. [Package repository contract](reference/contracts/package-repositories.md)

Contributors should read:

1. [Architecture](developer-guide/architecture.md)
2. [Runner and context](developer-guide/runner-and-context.md)
3. [Tool runtime](developer-guide/tool-runtime.md)
4. [Package installer](developer-guide/package-installer.md)

## Current Alpha Boundaries

- Python dependencies are host-owned and locked by the source checkout.
- Agent Core code slots run in the host-shared Python environment.
- Candidate Agent Core evolution cannot add dependencies automatically.
- Package recipes install files into runtime cores; they do not modify the host
  lock file.
- Release notes are preserved under [releases/](releases/0.3.3.md).
