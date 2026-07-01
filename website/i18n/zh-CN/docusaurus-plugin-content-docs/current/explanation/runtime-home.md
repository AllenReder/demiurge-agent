---
title: 运行时主目录
description: 理解 ~/.demiurge 下的本地 runtime 目录布局。
---

# 运行时主目录

Demiurge 是 local-first 的。Runtime 状态保存在 runtime home 下，通常是：

```text
~/.demiurge
```

源代码 checkout 和 runtime home 的职责不同。

## 主体布局

```text
~/.demiurge/
  config.yaml
  .env
  agents/
    agent.yaml
    assistant/
    evolver/
  sessions/
  scheduler/
  workspace/
  logs/
```

`config.yaml` 是 host-owned 的 runtime 配置。`.env` 可以保存本地 provider
密钥。`agents/` 包含运行中的 Agent Core。`sessions/` 保存持久化的 session 记录。
`scheduler/` 保存 scheduler 状态和运行记录。`workspace/` 是非本地的 fallback
workspace。

## 源模板与 Runtime Core

仓库中的源模板位于：

```text
agents/
```

`demiurge init` 会把这些模板复制或刷新到：

```text
~/.demiurge/agents/
```

如果要修改本地行为，就编辑 runtime cores。只有在你要修改默认打包项目行为时，才编辑
源模板。

## 托管 Checkout

Managed install 会把 checkout 放在：

```text
~/.demiurge/demiurge-agent
```

Live runtime cores 仍然是分开的，所以更新 managed checkout 不会覆盖已经编辑过的
Agent Core。

## 漂移

在刷新 runtime 文件之前，先做只读 drift 检查：

```bash
uv run demiurge init --check
uv run demiurge doctor
```

有意刷新时：

```bash
uv run demiurge init --refresh assistant
```
