---
title: Package Model
description: Understand package repositories, recipes, components, trust, and install state.
---

# Package Model

Demiurge packages are recipes for installing reusable files into runtime Agent
Cores.

They are not Python packages. They do not install dependencies. They do not
modify the host lock file.

Packages distribute capabilities. Agent Slots define where Core-defined behavior
enters the agent loop. A package can install slots together with tools, skills,
libraries, and child cores.

## Repository

A package repository is a directory or git checkout with:

```text
repository.yaml
packages/
bootstrap/
input/
output/
tool/
skill/
lib/
core/
```

Only `repository.yaml` and `packages/` are required. Component directories are
present when packages need those component kinds.

## Recipe

A recipe in `packages/<package_id>.yaml` declares:

- package identity
- options
- components
- conditions
- config to write into installed components
- pipeline edits
- manual dependency review warnings

## Components

Supported component kinds are:

- `bootstrap`
- `input`
- `output`
- `tool`
- `skill`
- `lib`
- `core`

Core-local components write into a target runtime core. `core` components create
or update another runtime active core by `target_core_id`.

## Trust

External repositories must be trusted before they can install local executable
code. Trust is local host policy, not something the package can grant to itself.

## Install State

Each target runtime core records installed package state in:

```text
packages.yaml
```

Secret option values are redacted there. Component-owned files can be removed by
uninstall. Data written outside component-owned targets is not deleted by
uninstall.
