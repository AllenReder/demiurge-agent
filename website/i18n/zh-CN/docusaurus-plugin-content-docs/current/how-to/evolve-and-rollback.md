---
title: 演化和回滚 Core
description: 使用 host-owned 的 evolution path 和 rollback controls。
---

# 演化和回滚 Core

Demiurge 可以让 host-managed 的 `evolver` core 编辑 runtime agents tree 的隔离 Git
worktree。Review 会创建 proposal commit；promote 会推进 live Git ref。

## 从 TUI 演化

在 TUI 内：

```text
/evolve Add a concise Telegram reply style input module.
```

Host 会创建 `.evolve/runs/<run_id>/agents`，使用 worktree-scoped tools 运行
`evolver` core，并返回 `run_id`。如果 `~/.demiurge/agents` 中有 local agent edits，
Demiurge 会先验证并保存这些 edits，然后从新的 live revision 创建 evolve worktree。
除此之外，start 不会改变 live core。

Review 该 run：

```text
/evolve review <run_id>
```

Review 会运行 host-owned gates，并创建或更新 `refs/demiurge/runs/<run_id>`。如果 MCP
declaration 发生变化，输出还会包含 secret-safe before/after diff 和内容绑定的
`mcp-review:<sha256>` token。

Promote 已 review 的 run：

```text
/evolve promote <run_id>
```

如果该 run 的 review 输出了 MCP security token，必须原样带上：

```text
/evolve promote <run_id> <mcp-review:sha256>
```

Promote 会重新运行 gates，推进 `refs/demiurge/previous` 和
`refs/demiurge/live`，并在下一 turn 生效。缺少所需 token 或使用旧 token 时会 fail
closed，不移动任何 ref。candidate 再次变化后应重新 review，并使用新 token。

对应的 CLI 命令是：

```bash
uv run demiurge core evolve promote <run_id> \
  --manual-review-token <mcp-review:sha256>
```

Promote 不会自动保存 local agent edits。如果 live agents tree 是 dirty 的，先用
`uv run demiurge core save` 保存，或用 `uv run demiurge core discard --yes` 丢弃，
然后再 promote。

丢弃不需要的 run：

```text
/evolve discard <run_id>
```

## 给出功能目标

好的 evolution goals 会描述行为和范围：

```text
Add an output module that emits a local Markdown artifact for long answers.
Change only agent/output and the output pipeline.
```

避免让 evolver 去编辑 host runtime code、dependencies、release files、source checkout
files 或 `.temp/` 的 goals。

## 查看 Revisions

在 TUI 内：

```text
/versions
```

## 回滚

在 TUI 内：

```text
/rollback
```

Rollback 会创建一个新的 rollback commit，把 agents tree 恢复到之前的 Git revision。
它会在下一 turn 生效。

Rollback 也会在存在 local agent edits 时拒绝执行。先用 `uv run demiurge core diff`
检查 edits，然后 save 或 discard，再 rollback。

需要指定 target 时：

```text
/rollback <revision>
```

## 规则

精确规则见
[/docs/reference/contracts/evolver-safe-edits](/docs/reference/contracts/evolver-safe-edits)。

evolver 可以编辑 isolated agents-tree worktree 内的 authored surface。它不能 promote、
roll back、编辑 host state、修改 dependencies，或者编辑该 isolated worktree 之外的文件。
