---
title: 安全模型
description: 理解 host-owned capabilities、approvals、secrets 和 workspace scope。
---

# 安全模型

Demiurge 把 capabilities 视为 host-owned。Agent Core code 可以请求效果，但是否执行由
host 决定。

## Workspace Scope

File writes、patches 和 terminal working directories scoped 到 resolved workspace。Workspace 可以来自 process override、environment variable、core manifest、local run context，或 fallback `~/.demiurge/workspace`。

Built-in file reads 可以指向 workspace 外的 host-visible paths。Workspace 外 reads，以及所有 sensitive reads，都会在打开文件前要求 approval。

## Capability 边界

这些效果必须经过 host-owned interfaces：

- 文件系统读写
- 终端命令
- 网络获取
- 状态变更
- agent evolution
- Git revision promotion 和 rollback

声明 capability 不等于获得权限。Host 会在执行前检查 capabilities 和 approval policy。

## Approval Policy

Approval policy 可以配置在：

- global host config
- Agent Core manifest
- tool metadata
- risk defaults

高风险操作应该默认需要 approval。拒绝时，host 不应该把高风险效果交给 Agent Core
绕过执行。

## Secrets

Provider secrets 应该放在 host config、environment variables 或 `~/.demiurge/.env`。
Packages 可以把 secret option 写入 installed component config，但 `packages.yaml` 只
记录 `<redacted>`。其中的 provenance hashes 用于 drift reporting 和 uninstall
safety；runtime truth 仍然是已提交的 agents tree。

Slots 和 tools 不应该打印 secrets。

## Workspace

Workspace scope 限制 filesystem 和 terminal operations。Core-authored code 不应该假设
可以访问任意本地路径。

## Channel Allowlist

Telegram 默认 deny。必须通过 `allowed_users` 或 `allowed_chats` 明确允许。

## Non-Goals

当前 alpha runtime 不承诺 hardened multi-tenant sandbox。Agent Slot code 默认运行在
host-shared Python environment 中。Per-core environments 和 subprocess workers 是未来
isolation options，不是当前默认模式。Runtime task records、logs、scheduler
instances 和 delivery outbox status 存在 SQLite runtime database 中；in-process
workers 仍负责 live execution。
