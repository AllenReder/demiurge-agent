---
title: Install Packages
description: Preview, install, list, and uninstall reusable Agent Core packages.
---

# Install Packages

Packages install reusable components into runtime Agent Cores. They can install
bootstrap modules, input modules, output modules, tools, skills, libraries, and
child cores.

## List Packages

```bash
uv run demiurge package list --core assistant
uv run demiurge package list --repo builtin
```

## Preview an Install

```bash
uv run demiurge package install memory_basic --core assistant --preview
```

Use preview before installing packages that add code slots, tools, or external
provider integration.

## Install

```bash
uv run demiurge package install memory_basic --core assistant
```

Use a repository-qualified package id when package names are ambiguous:

```bash
uv run demiurge package install builtin/memory_basic --core assistant
```

Pass options with repeated `--option` flags:

```bash
uv run demiurge package install minimax_tts \
  --core assistant \
  --option mode=summary \
  --option enable_tool=true
```

Provider-owned web search packages expose the same model-facing tool name,
`web_search`:

```bash
uv run demiurge package install web_search_brave --core assistant --preview
uv run demiurge package install web_search_tavily --core assistant --preview
```

Because both packages target `agent/tools/web_search`, install only one web
search provider package in a core at a time. To switch providers, uninstall the
current web search package first.

## Uninstall

```bash
uv run demiurge package uninstall memory_basic --core assistant --preview
uv run demiurge package uninstall memory_basic --core assistant
```

Uninstall removes package-owned component targets and updates `packages.yaml`.
It does not remove package data written outside owned targets.

## Add an External Repository

```bash
uv run demiurge package repo add https://github.com/user/demiurge-packages.git \
  --alias community \
  --ref main \
  --trust
```

For a local repository:

```bash
uv run demiurge package repo add ./local-packages --alias local --trust
```

Trust is explicit because repositories can install executable local code.

## Verify

```bash
uv run demiurge package list --core assistant
uv run demiurge init --check
uv run demiurge --provider fake
```

If the package installs a tool, inspect the visible tool registry:

```text
/tools
```

## Boundary

Package management is a user-controlled CLI workflow. It is not an agent-callable
model tool. Package recipes do not install Python dependencies or edit the host
`uv.lock`.
