---
title: Package 模型
description: 理解 package repositories、recipes、components、trust 和 install state。
---

# Package 模型

Demiurge packages 是把可复用文件安装进 runtime Agent Core 的 recipes。

它们不是 Python packages。它们不安装 dependencies。它们不修改 host lock file。

Packages 分发能力。Agent Slots 定义 Core 定义的行为逻辑在哪里进入 agent loop。
一个 package 可以把 slots、tools、skills、libraries 和 child cores 一起安装。

## Repository

Package repository 是一个目录或 git checkout，包含：

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

只有 `repository.yaml` 和 `packages/` 是必需的。Component directories 只在 package
需要对应 component kind 时出现。

## Recipe

`packages/<package_id>.yaml` 中的 recipe 声明：

- package identity
- options
- components
- conditions
- 要写入 installed components 的 config
- pipeline edits
- manual dependency review warnings

## Components

支持的 component kinds：

- `bootstrap`
- `input`
- `output`
- `tool`
- `skill`
- `lib`
- `core`

Core-local components 会写入目标 runtime core。`core` components 会通过
`target_core_id` 创建或更新另一个 runtime active core。

## Trust

External repositories 必须先被 trust，才能安装本地 executable code。Trust 是本地 host
policy，不是 package 可以自授予的东西。

## 安装状态

每个目标 runtime core 会在这里记录 installed package state：

```text
packages.yaml
```

Secret option values 会被 redacted。Uninstall 可以移除 component-owned files。写在
component-owned targets 之外的数据不会被 uninstall 删除。
